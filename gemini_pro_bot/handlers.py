import asyncio
import re
from gemini_pro_bot.llm import model, img_model
from google.generativeai.types.generation_types import (
    StopCandidateException,
    BlockedPromptException,
)
from telegram import Update
from telegram.ext import (
    ContextTypes,
)
from telegram.error import NetworkError, BadRequest
from telegram.constants import ChatAction, ParseMode
from gemini_pro_bot.html_format import format_message
import PIL.Image as load_image
from io import BytesIO
import telegram

# --- Функции для разбивки и очистки текста ---

def split_message(text, max_length=4000):
    """Разбивает длинное сообщение на части по max_length символов."""
    parts = []
    while len(text) > max_length:
        split_pos = text.rfind('\n', 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind('. ', 0, max_length)
            if split_pos != -1:
                split_pos += 2
        if split_pos == -1:
            split_pos = max_length
        part = text[:split_pos]
        text = text[split_pos:]
        parts.append(part)
    if text:
        parts.append(text)
    return parts

def sanitize_html(message: str) -> str:
    # Удалить незакрытые/перепутанные теги
    message = re.sub(r'<[^>]+$', '', message)
    message = re.sub(r'</[biu]>', '', message)
    return message

async def safe_send(send_method, *args, **kwargs):
    try:
        return await send_method(*args, **kwargs)
    except telegram.error.Forbidden:
        print("User blocked the bot. Message skipped.")
    except telegram.error.BadRequest as e:
        print(f"BadRequest: {e}")
    except Exception as e:
        print(f"Other send message error: {e}")

def new_chat(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data["chat"] = model.start_chat()

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await safe_send(
        update.message.reply_html,
        f"Hi {user.mention_html()}!\n\nStart sending messages with me to generate a response.\n\nSend /new to start a new chat session.",
    )

async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
Basic commands:
/start - Start the bot
/help - Get help. Shows this message

Chat commands:
/new - Start a new chat session (model will forget previously generated messages)

Send a message to the bot to generate a response.
"""
    await safe_send(update.message.reply_text, help_text)

async def newchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    init_msg = await safe_send(
        update.message.reply_text,
        text="Starting new chat session...",
        reply_to_message_id=update.message.message_id,
    )
    new_chat(context)
    await safe_send(init_msg.edit_text, "New chat session started.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.chat_data.get("chat") is None:
        new_chat(context)
    text = update.message.text
    init_msg = await safe_send(
        update.message.reply_text,
        text="Generating...", reply_to_message_id=update.message.message_id
    )
    await update.message.chat.send_action(ChatAction.TYPING)
    chat = context.chat_data.get("chat")
    response = None
    try:
        response = await chat.send_message_async(text, stream=True)
    except StopCandidateException as sce:
        print("Prompt: ", text, " was stopped. User: ", update.message.from_user)
        print(sce)
        await safe_send(init_msg.edit_text, "The model unexpectedly stopped generating.")
        chat.rewind()
        return
    except BlockedPromptException as bpe:
        print("Prompt: ", text, " was blocked. User: ", update.message.from_user)
        print(bpe)
        await safe_send(init_msg.edit_text, "Blocked due to safety concerns.")
        if response:
            await response.resolve()
        return

    full_plain_message = ""
    async for chunk in response:
        try:
            if chunk.text:
                full_plain_message += chunk.text
                message = format_message(full_plain_message)
                message = sanitize_html(message)

                # Разбиваем и шлём по частям (длина и HTML под контролем)
                parts = split_message(message)
                for i, part in enumerate(parts):
                    if len(part.strip()) == 0:
                        continue
                    if i == 0:
                        await safe_send(
                            init_msg.edit_text,
                            text=part,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    else:
                        await safe_send(
                            update.message.reply_text,
                            text=part,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
        except StopCandidateException as sce:
            await safe_send(init_msg.edit_text, "The model unexpectedly stopped generating.")
            chat.rewind()
            continue
        except BadRequest:
            await response.resolve()
            continue
        except NetworkError:
            raise NetworkError(
                "Looks like you're network is down. Please try again later."
            )
        except IndexError:
            await safe_send(
                init_msg.reply_text,
                "Some index error occurred. This response is not supported."
            )
            await response.resolve()
            continue
        except Exception as e:
            print(e)
            if chunk.text:
                msg = sanitize_html(format_message(chunk.text))
                await safe_send(
                    update.message.reply_text,
                    text=msg,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=init_msg.message_id,
                    disable_web_page_preview=True,
                )
        await asyncio.sleep(0.1)

async def handle_image(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    init_msg = await safe_send(
        update.message.reply_text,
        text="Generating...", reply_to_message_id=update.message.message_id
    )
    images = update.message.photo
    unique_images: dict = {}
    for img in images:
        file_id = img.file_id[:-7]
        if file_id not in unique_images:
            unique_images[file_id] = img
        elif img.file_size > unique_images[file_id].file_size:
            unique_images[file_id] = img
    file_list = list(unique_images.values())
    file = await file_list[0].get_file()
    a_img = load_image.open(BytesIO(await file.download_as_bytearray()))
    prompt = update.message.caption if update.message.caption else "Analyse this image and generate response"
    response = await img_model.generate_content_async([prompt, a_img], stream=True)
    full_plain_message = ""
    async for chunk in response:
        try:
            if chunk.text:
                full_plain_message += chunk.text
                message = format_message(full_plain_message)
                message = sanitize_html(message)
                parts = split_message(message)
                for i, part in enumerate(parts):
                    if len(part.strip()) == 0:
                        continue
                    if i == 0:
                        await safe_send(
                            init_msg.edit_text,
                            text=part,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    else:
                        await safe_send(
                            update.message.reply_text,
                            text=part,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
        except StopCandidateException:
            await safe_send(init_msg.edit_text, "The model unexpectedly stopped generating.")
        except BadRequest:
            await response.resolve()
            continue
        except NetworkError:
            raise NetworkError(
                "Looks like you're network is down. Please try again later."
            )
        except IndexError:
            await safe_send(
                init_msg.reply_text,
                "Some index error occurred. This response is not supported."
            )
            await response.resolve()
            continue
        except Exception as e:
            print(e)
            if chunk.text:
                msg = sanitize_html(format_message(chunk.text))
                await safe_send(
                    update.message.reply_text,
                    text=msg,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=init_msg.message_id,
                    disable_web_page_preview=True,
                )
        await asyncio.sleep(0.1)
