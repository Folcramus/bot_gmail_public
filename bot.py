import time
import asyncio
from gmail_client import GmailClient
from telegram_client import TelegramClient
from config import Config, setup_logging
from typing import Dict, Any
from collections import defaultdict
from datetime import datetime

logger = setup_logging()


class MailForwarderBot:
    def __init__(self):
        self.gmail = GmailClient()
        self.telegram = TelegramClient()
        self.labels = list(Config.LABEL_TO_THREAD_MAPPING.keys())
        self.loop = asyncio.get_event_loop()
        self._validate_labels()

        self.processed_messages = set()
        self.message_queue = asyncio.Queue()
        self.last_send_times = defaultdict(lambda: datetime.min)
        self.sending_task = None
        self.sending_lock = asyncio.Lock()

        self.processed_messages = set()  # Для отслеживания уже обработанных сообщений

    async def start_message_sender(self):
        """Запускает фоновую задачу для отправки сообщений"""
        if self.sending_task is None:
            self.sending_task = asyncio.create_task(self._message_sender_worker())

    async def _message_sender_worker(self):
        """Фоновый процесс для отправки сообщений с интервалом"""
        while True:
            try:
                thread_id, formatted_msg = await self.message_queue.get()

                async with self.sending_lock:
                    # Вычисляем время ожидания
                    now = datetime.now()
                    last_send = self.last_send_times[thread_id]
                    time_since_last = (now - last_send).total_seconds()
                    wait_time = max(0, 5 - time_since_last)

                    if wait_time > 0:
                        await asyncio.sleep(wait_time)

                    # Отправляем сообщение
                    message_sent = await self.telegram.send_message_to_thread(
                        thread_id, formatted_msg
                    )
                    self.last_send_times[thread_id] = datetime.now()

                    if not message_sent:
                        # Если не удалось отправить, возвращаем в очередь
                        await self.message_queue.put((thread_id, formatted_msg))
                        await asyncio.sleep(5)  # Подождем перед повторной попыткой

            except Exception as e:
                logger.error(f"Ошибка в worker отправки сообщений: {e}")
                await asyncio.sleep(5)
    def _validate_labels(self):
        """Проверяет, существуют ли все указанные метки в Gmail"""
        missing_labels = []
        for label in self.labels:
            if not self.gmail.get_label_id(label):
                missing_labels.append(label)

        if missing_labels:
            logger.error(f"Следующие метки не найдены в Gmail: {missing_labels}")
            raise ValueError(f"Отсутствуют метки в Gmail: {missing_labels}")

    def _get_thread_id_for_message(self, message: Dict[str, Any]) -> int:
        """Определяет ID топика Telegram на основе меток сообщения"""
        metadata = self.gmail.get_message_metadata(message['id'])
        if not metadata:
            logger.warning(f"Не удалось получить метаданные для сообщения {message['id']}")
            return None

        label_ids = metadata.get('labelIds', [])

        for label_name, thread_id in Config.LABEL_TO_THREAD_MAPPING.items():
            label_id = self.gmail.get_label_id(label_name)
            if label_id and label_id in label_ids:
                logger.debug(f"Найдено соответствие: метка '{label_name}' → топик {thread_id}")
                return thread_id

        logger.warning(
            f"Не найдено соответствия для сообщения {message['id']}. "
            f"Метки сообщения: {label_ids}. "
            f"Доступные соответствия: {Config.LABEL_TO_THREAD_MAPPING}"
        )
        return None

    async def _process_single_message(self, msg):
        """Асинхронно обрабатывает одно сообщение"""
        msg_id = msg['id']
        try:
            if msg_id in self.processed_messages:
                logger.debug(f"Сообщение {msg_id} уже обработано, пропускаем")
                return

            logger.debug(f"Обработка сообщения ID: {msg_id}")

            full_message = self.gmail.get_message_details(msg_id)
            if not full_message:
                logger.error(f"Не удалось получить содержимое сообщения {msg_id}")
                return

            thread_id = self._get_thread_id_for_message(msg)
            if not thread_id:
                logger.warning(f"Не найден топик для сообщения {msg_id}")
                return

            # Форматируем сообщение и добавляем в очередь
            formatted_msg = self.telegram.format_message(full_message)
            await self.message_queue.put((thread_id, formatted_msg))
            logger.info(f"Сообщение {msg_id} добавлено в очередь для топика {thread_id}")

            # Отправляем вложения (если есть) - тоже через очередь
            for attachment in full_message.get('attachments', []):
                await self.message_queue.put((thread_id, attachment))

            # Помечаем как прочитанное в Gmail
            if not self.gmail.mark_as_read(msg_id):
                logger.error(f"Не удалось пометить сообщение {msg_id} как прочитанное")
                return

            self.processed_messages.add(msg_id)
            logger.info(f"Сообщение {msg_id} успешно обработано")

        except Exception as e:
            logger.error(f"Ошибка при обработке сообщения {msg_id}: {str(e)}")
            try:
                self.gmail.mark_as_read(msg_id)
            except Exception as mark_error:
                logger.error(f"Не удалось пометить сообщение {msg_id} как прочитанное: {str(mark_error)}")

    async def process_all_messages(self):
        """Обрабатывает ВСЕ сообщения с указанными метками"""
        logger.info("Начало обработки ВСЕХ сообщений с указанными метками...")

        # Получаем все сообщения (без ограничения по количеству)
        try:
            label_ids = [self.gmail.get_label_id(name) for name in self.labels]
            label_ids = [lid for lid in label_ids if lid is not None]

            if not label_ids:
                logger.error("Не найдено ни одного из запрошенных ярлыков")
                return

            # Получаем все сообщения с указанными метками
            messages = []
            page_token = None
            while True:
                results = self.gmail.service.users().messages().list(
                    userId='me',
                    labelIds=label_ids,
                    maxResults=500,
                    pageToken=page_token
                ).execute()

                messages.extend(results.get('messages', []))
                page_token = results.get('nextPageToken')
                if not page_token:
                    break

            logger.info(f"Всего найдено {len(messages)} сообщений для обработки")

            # Обрабатываем все сообщения
            tasks = [self._process_single_message(msg) for msg in messages]
            await asyncio.gather(*tasks)

        except Exception as e:
            logger.error(f"Ошибка при получении всех сообщений: {e}")

    async def process_new_messages(self):
        """Обрабатывает только новые сообщения"""
        logger.info("Проверка новых сообщений...")
        messages = self.gmail.get_messages_with_labels(self.labels)

        if not messages:
            logger.debug("Новых сообщений не найдено")
            return

        logger.info(f"Найдено {len(messages)} новых сообщений для обработки")

        tasks = [self._process_single_message(msg) for msg in messages]
        await asyncio.gather(*tasks)

    async def run(self):
        """Основной асинхронный цикл работы бота"""
        logger.info("Запуск Mail Forwarder Bot")

        # Запускаем фоновую задачу для отправки сообщений
        await self.start_message_sender()

        # Сначала обрабатываем все существующие сообщения
        await self.process_all_messages()

        # Затем переходим к периодической проверке новых сообщений
        try:
            while True:
                start_time = time.time()
                await self.process_new_messages()

                elapsed = time.time() - start_time
                sleep_time = max(0, Config.CHECK_INTERVAL - elapsed)
                await asyncio.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info("Бот остановлен пользователем")
        except Exception as e:
            logger.error(f"Критическая ошибка в боте: {e}")
            raise


if __name__ == '__main__':
    bot = MailForwarderBot()
    try:
        asyncio.run(bot.run())
    except Exception as e:
        logger.critical(f"Не удалось запустить бота: {e}")
        raise