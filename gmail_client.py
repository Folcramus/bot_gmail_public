import base64
import email
import logging
from typing import List, Dict
from config import Config
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)


def _extract_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == 'text/plain':
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or 'utf-8'
                return payload.decode(charset, errors='replace')
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or 'utf-8'
        return payload.decode(charset, errors='replace')
    return ''


def _get_header(msg, header_name):
    header = msg.get(header_name, '')
    if header:
        decoded = email.header.decode_header(header)
        return ''.join(
            str(t[0], t[1] or 'utf-8') if isinstance(t[0], bytes) else str(t[0])
            for t in decoded
        )
    return ''


class GmailClient:
    def __init__(self):
        self.creds = Credentials(
            token=None,
            refresh_token=Config.GMAIL_REFRESH_TOKEN,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=Config.GMAIL_CLIENT_ID,
            client_secret=Config.GMAIL_CLIENT_SECRET
        )
        self.service = build('gmail', 'v1', credentials=self.creds)

    def _refresh_token(self):
        try:
            self.creds.refresh(Request())
            logger.info("Gmail token refreshed successfully")
            return True
        except Exception as e:
            logger.error(f"Error refreshing Gmail token: {e}")
            return False

    def get_label_id(self, label_name):
        try:
            results = self.service.users().labels().list(userId='me').execute()
            labels = results.get('labels', [])
            for label in labels:
                if label['name'].lower() == label_name.lower():
                    return label['id']
            return None
        except Exception as e:
            logger.error(f"Error getting label ID: {e}")
            return None

    def get_message_metadata(self, msg_id: str) -> Dict:
        """Получает метаданные сообщения (включая labels)"""
        try:
            return self.service.users().messages().get(
                userId='me',
                id=msg_id,
                format='metadata',
                metadataHeaders=['labels']
            ).execute()
        except Exception as e:
            logger.error(f"Error getting message metadata: {e}")
            return {}

    def get_messages_with_labels(self, label_names: List[str]) -> List[Dict]:
        """
        Получает непрочитанные сообщения с указанными метками

        Args:
            label_names: Список названий меток для поиска

        Returns:
            Список сообщений в формате Gmail API
        """
        # 1. Проверка и обновление токена
        if not self._refresh_token():
            logger.error("Не удалось обновить токен доступа")
            return []

        # 2. Получение ID меток с проверкой
        label_info = []
        for name in label_names:

            label_id = self.get_label_id(name)
            if not label_id:
                logger.warning(f"Метка '{name}' не найдена в аккаунте")
                continue

            # Получаем статистику по метке
            try:
                stats = self.service.users().labels().get(
                    userId='me',
                    id=label_id
                ).execute()
                label_info.append({
                    'name': name,
                    'id': label_id,
                    'total': stats.get('messagesTotal', 0),
                    'unread': stats.get('messagesUnread', 0)
                })
            except Exception as e:
                logger.error(f"Ошибка получения статистики для метки '{name}': {e}")
                continue

        # 3. Проверка наличия непрочитанных сообщений
        if not label_info:
            logger.error("Не найдено ни одной доступной метки")
            return []

        logger.info("Статистика по меткам:")
        for info in label_info:
            logger.info(
                f"Метка: {info['name']} (ID: {info['id']})\n"
                f"• Всего сообщений: {info['total']}\n"
                f"• Непрочитанных: {info['unread']}"
            )

        # 4. Получение непрочитанных сообщений
        try:
            # Вариант 1: Стандартный запрос
            results = self.service.users().messages().list(
                userId='me',
                labelIds=[info['id'] for info in label_info],
                q="is:unread",
                maxResults=50  # Лимит для теста
            ).execute()

            messages = results.get('messages', [])

            # Если сообщений не найдено, пробуем альтернативный метод
            if not messages:
                logger.info("Пробуем альтернативный метод поиска...")
                for info in label_info:
                    if info['unread'] > 0:
                        # Вариант 2: Поиск по каждой метке отдельно
                        results = self.service.users().messages().list(
                            userId='me',
                            labelIds=[info['id']],
                            q="is:unread"
                        ).execute()
                        messages.extend(results.get('messages', []))

            # 5. Диагностика найденных сообщений
            if messages:
                logger.info(f"Найдено непрочитанных сообщений: {len(messages)}")

                # Логируем информацию о первых 3 сообщениях
                for msg in messages[:3]:
                    try:
                        msg_data = self.service.users().messages().get(
                            userId='me',
                            id=msg['id'],
                            format='metadata',
                            metadataHeaders=['subject', 'from', 'date']
                        ).execute()
                        logger.debug(
                            "Пример сообщения:\n"
                            f"ID: {msg['id']}\n"
                            f"Тема: {msg_data.get('subject', 'Нет темы')}\n"
                            f"От: {msg_data.get('from', 'Нет отправителя')}\n"
                            f"Дата: {msg_data.get('date', 'Нет даты')}"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка получения данных сообщения: {e}")
            else:
                logger.info("Непрочитанных сообщений не найдено")

            return messages

        except Exception as e:
            logger.error(f"Ошибка при поиске сообщений: {e}", exc_info=True)
            return []

    def get_message_details(self, msg_id):
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=msg_id,
                format='raw'
            ).execute()

            msg_str = base64.urlsafe_b64decode(message['raw'].encode('ASCII'))
            mime_msg = email.message_from_bytes(msg_str)

            # Extract basic info
            subject = _get_header(mime_msg, 'Subject')
            from_ = _get_header(mime_msg, 'From')
            date = _get_header(mime_msg, 'Date')

            # Extract body
            body = _extract_body(mime_msg)

            # Extract attachments
            attachments = self._extract_attachments(mime_msg)

            return {
                'subject': subject,
                'from': from_,
                'date': date,
                'body': body,
                'attachments': attachments,
                'id': msg_id
            }
        except Exception as e:
            logger.error(f"Error getting message details for {msg_id}: {e}")
            return None

    def mark_as_read(self, msg_id):
        try:
            self.service.users().messages().modify(
                userId='me',
                id=msg_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
            logger.info(f"Marked message {msg_id} as read")
            return True
        except Exception as e:
            logger.error(f"Error marking message {msg_id} as read: {e}")
            return False

    @staticmethod
    def _extract_attachments(msg):
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                if part.get('Content-Disposition') is None:
                    continue

                filename = part.get_filename()
                if filename:
                    file_data = part.get_payload(decode=True)
                    attachments.append({
                        'filename': filename,
                        'data': file_data,
                        'mime_type': part.get_content_type()
                    })
        return attachments
