from telegram import Bot, InputFile
from telegram.error import TelegramError
from telegram.request import HTTPXRequest
from bs4 import BeautifulSoup
from config import Config
import logging
from io import BytesIO
import re
logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self):
        self.trequest = HTTPXRequest(connection_pool_size=20)
        self.bot = Bot(token=Config.TELEGRAM_BOT_TOKEN, request=self.trequest)
        self.group_id = Config.TELEGRAM_GROUP_ID

    async def send_message_to_thread(self, thread_id, text):
        try:
            message = await self.bot.send_message(
                chat_id=self.group_id,
                text=text,
                message_thread_id=thread_id,
                parse_mode='MarkdownV2',

            )
            logger.info(f"Сообщение отправлено в топик {thread_id}")
            return message
        except TelegramError as e:
            logger.error(f"Ошибка отправки сообщения в топик {thread_id}: {e}")
            return None

    async def send_attachment_to_thread(self, thread_id, attachment):
        try:
            file = BytesIO(attachment['data'])
            file.name = attachment['filename']

            if attachment['mime_type'].startswith('image/'):
                sent_msg = await self.bot.send_photo(
                    chat_id=self.group_id,
                    photo=InputFile(file),
                    message_thread_id=thread_id
                )
            elif attachment['mime_type'] == 'application/pdf':
                sent_msg = await self.bot.send_document(
                    chat_id=self.group_id,
                    document=InputFile(file),
                    message_thread_id=thread_id
                )
            else:
                sent_msg = await self.bot.send_document(
                    chat_id=self.group_id,
                    document=InputFile(file),
                    message_thread_id=thread_id,
                    caption=f"Файл: {attachment['filename']}"
                )

            logger.info(f"Вложение {attachment['filename']} отправлено в топик {thread_id}")
            return sent_msg
        except TelegramError as e:
            logger.error(f"Ошибка отправки вложения в топик {thread_id}: {e}")
            return None

    def format_message(self, message_details):
        """
        Основной метод форматирования сообщения с автоматическим определением типа
        Поддерживает:
        - Обычные платежи
        - Операции по карте
        - Входящие зачисления
        - Переводы через СБП
        - Любые другие сообщения (выводит как есть)
        """
        try:
            body = message_details.get('body', '')
            if not body or not isinstance(body, str):
                return "Ошибка: отсутствует тело сообщения или неверный формат"

            # Очистка и нормализация текста
            body = self._preprocess_html(body)
            soup = BeautifulSoup(body, 'html.parser')
            self._remove_unwanted_elements(soup)
            text = self._extract_clean_text(soup)

            # Определяем тип сообщения (ВАЖНО: порядок проверки имеет значение!)
            if self._is_incoming_payment(text):
                payment_data = self._parse_incoming_payment(text)
                return self._create_incoming_payment_message(payment_data)
            elif self._is_sbp_payment(text):  # Проверяем СБП ДО карточных операций!
                payment_data = self._parse_sbp_payment(text)
                return self._create_sbp_payment_message(payment_data)
            elif self._is_card_operation(text):
                payment_data = self._parse_card_operation(text)
                return self._create_card_operation_message(payment_data)
            else:
                try:
                    # Пробуем распарсить как обычный платеж
                    payment_data = self._parse_payment_text(text)
                    formatted = self._create_payment_message(payment_data)

                    if payment_data.get('amount') and payment_data.get('recipient'):
                        return formatted
                    else:
                        # Если не удалось извлечь ключевые данные, возвращаем оригинальный текст в экранированном виде
                        return self._escape_markdown(text)
                except Exception:
                    return self._escape_markdown(text)

        except Exception as e:
            print(f"Ошибка при обработке сообщения: {e}")
            return self._escape_markdown(message_details.get('body', 'Не удалось обработать сообщение'))

    def _is_card_operation(self, text):
        """Определяет, является ли сообщение операцией по карте"""
        card_indicators = [
            'Карта *',
            'Снятие',
            'Пополнение',
            'Остаток',
            'Баланс:'
        ]
        exclude_indicators  = [
            'Зачислен платёж',
            'пришёл платёж',
            'На ваш счёт',
            'Отправитель —',
            'через Систему быстрых платежей', # ИСКЛЮЧАЕМ: если есть эта фраза, это не операция по карте!
            'по номеру телефона',
            'Т-Банк'# ИСКЛЮЧАЕМ: если есть эта фраза, это не операция по карте!

        ]
        return (any(indicator in text for indicator in card_indicators) and \
                not any(exclude in text for exclude in exclude_indicators))

    def _is_incoming_payment(self, text):
        """Определяет, является ли сообщение входящим платежом"""
        indicators = [
            'Зачислен платёж',
            'пришёл платёж',
            'На ваш счёт',
            'Отправитель —'
        ]

        return any(indicator in text for indicator in indicators)

    def _parse_card_operation(self, text):
        """Парсит данные для операций по карте"""
        payment_data = {}

        # Тип операции
        operation_match = re.search(r'(Снятие|Пополнение|Оплата|Перевод)', text, re.IGNORECASE)
        payment_data['operation'] = operation_match.group(1) if operation_match else 'Операция'

        # Сумма
        amount_match = re.search(r'(?:Снятие|Пополнение|Оплата|Перевод)\s*(\d[\d \.,]+)\s*[₽р]', text, re.IGNORECASE) or \
                       re.search(r'(\d[\d \.,]+)\s*[₽р](?=\s*в\s)', text)
        if amount_match:
            payment_data['amount'] = self._format_number(amount_match.group(1))

        # Место операции
        place_match = re.search(r'(?:в|через|на)\s+([A-Za-zА-Яа-я0-9]+)', text, re.IGNORECASE)
        payment_data['place'] = place_match.group(1) if place_match else ''

        # Номер карты
        card_match = re.search(r'Карта\s*\*(\d{4})', text)
        payment_data['card'] = f"*{card_match.group(1)}" if card_match else ''

        # Остаток
        balance_match = re.search(r'Остаток\s*(\d[\d \.,]+)\s*[₽р]', text) or \
                        re.search(r'Баланс:\s*(\d[\d \.,]+)\s*[₽р]', text)
        if balance_match:
            payment_data['balance'] = self._format_number(balance_match.group(1))

        return payment_data

    def _parse_incoming_payment(self, text):
        """Парсит данные для входящих платежей"""
        payment_data = {}

        # Сумма платежа
        amount_match = re.search(r'платёж №\d+ на ([\d \.,]+) RUB', text) or \
                       re.search(r'Сумма платежа — ([\d \.,]+) RUB', text) or \
                       re.search(r'на ([\d \.,]+) RUB', text)
        if amount_match:
            payment_data['amount'] = self._format_number(amount_match.group(1))

        # Отправитель
        sender_match = re.search(r'Отправитель — (.+?)(?:,|\n|$)', text)
        if sender_match:
            payment_data['sender'] = sender_match.group(1).strip()

        # Назначение платежа
        purpose_match = re.search(r'Назначение — (.+?)(?:\n|$)', text, re.DOTALL)
        if purpose_match:
            payment_data['purpose'] = purpose_match.group(1).strip()

        # Остаток на счете
        balance_match = re.search(r'счёте — ([\d \.,]+) RUB', text) or \
                        re.search(r'Остаток ([\d \.,]+) RUB', text)
        if balance_match:
            payment_data['balance'] = self._format_number(balance_match.group(1))

        # Счет получателя
        account_match = re.search(r'счёт (\d+)', text) or \
                        re.search(r'счет[ае]? (\d+)', text, re.IGNORECASE)
        if account_match:
            payment_data['account'] = account_match.group(1)

        return payment_data

    def _is_sbp_payment(self, text):
        """Определяет, является ли сообщение переводом через СБП"""
        indicators = [
            'через Систему быстрых платежей',
            'по номеру телефона',
            'Мы отправили',  # Но только в комбинации с другими признаками
            'Банк получателя —'
        ]
        # Проверяем, что есть несколько признаков, чтобы избежать ложных срабатываний
        found_indicators = sum(1 for indicator in indicators if indicator in text)
        return found_indicators >= 2  # Например, если найдено至少 2 признака

    def _parse_payment_text(self, text):
        """Парсит данные для обычных платежей"""
        payment_data = {}

        # Номер платежа
        payment_num_match = re.search(r'Платёж №(\d+)', text)
        if payment_num_match:
            payment_data['number'] = payment_num_match.group(1)

        # Сумма платежа
        amount_match = re.search(r'(?:на|сумма)\s*[—-]\s*(\d[\d \.,]+)\s*RUB', text, re.IGNORECASE)
        if amount_match:
            payment_data['amount'] = self._format_number(amount_match.group(1))

        # Счет отправителя
        account_match = re.search(r'со\s+сч[ёе]та\s*(\d+)', text, re.IGNORECASE)
        if account_match:
            payment_data['account'] = account_match.group(1)

        # Получатель
        recipient_match = re.search(r'Получатель\s*[—-]\s*(.+?)(?:\s*[,;\n]|$)', text, re.IGNORECASE)
        if recipient_match:
            recipient_text = recipient_match.group(1).strip()
            # Ищем ФИО с сохранением ИП
            fio_match = re.search(
                r'(ИП\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)|([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)',
                recipient_text
            )
            if fio_match:
                payment_data['recipient'] = fio_match.group(1) or fio_match.group(2)
            else:
                payment_data['recipient'] = recipient_text
        else:
            payment_data['recipient'] = 'ИП Смирнов Руслан Владимирович'

        # Остаток на счете
        balance_match = re.search(
            r'(?:сч[ёе]те?|баланс)[\s:—-]*([\d \.,]+)\s*RUB',
            text,
            re.IGNORECASE
        )
        if not balance_match:
            balance_match = re.search(
                r'(?:на|ваш[её]м?)\s+сч[ёе]те?\s*[—-]\s*([\d \.,]+)\s*RUB',
                text,
                re.IGNORECASE
            )
        if balance_match:
            payment_data['balance'] = self._format_number(balance_match.group(1).strip())

        # Назначение платежа
        purpose_match = re.search(r'Назначение\s*[—-]\s*(.+?)(?:\n|$)', text, re.DOTALL | re.IGNORECASE)
        if purpose_match:
            payment_data['purpose'] = purpose_match.group(1).strip()

        # Дата и время
        date_match = re.search(r'Время\s+(?:отправки|операции)\s*[—-]\s*(.+?)(?:\s*\(|$)', text, re.IGNORECASE)
        if date_match:
            payment_data['date'] = date_match.group(1).strip()

        return payment_data

    def _parse_sbp_payment(self, text):
        """Парсит данные для перевода через СБП"""
        payment_data = {}

        # Сумма (более универсальный паттерн)
        amount_match = re.search(r'(?:Мы отправили|Сумма\s*[—-])\s*([\d \.,]+)\s*[₽р]', text)
        if amount_match:
            payment_data['amount'] = self._format_number(amount_match.group(1))

        # Получатель (более надежное извлечение)
        recipient_match = re.search(r'Получатель\s*[—-]\s*([^\n\r]+)', text)
        if recipient_match:
            payment_data['recipient'] = recipient_match.group(1).strip()

        # Банк получателя
        bank_match = re.search(r'Банк получателя\s*[—-]\s*([^\n\r]+)', text)
        if bank_match:
            payment_data['recipient_bank'] = bank_match.group(1).strip()

        return payment_data

    def _create_sbp_payment_message(self, payment_data):
        """
        Форматирует сообщение о переводе через СБП в чистом виде:
        10 000 - Виталий Иванович П.
        Банк получателя — Озон Банк (Ozon)
        """

        def escape_md(text):
            if not text:
                return ""
            return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

        amount = escape_md(payment_data.get('amount', '0,00'))
        recipient = escape_md(payment_data.get('recipient', 'не указан'))

        bank = escape_md(payment_data.get('recipient_bank', ''))

        # Собираем сообщение в нужном формате
        line1 = f"{amount} — {recipient}"
        line2 = f"Банк получателя — {bank}" if bank else ""
        line3 = f"\#СБП"

        return '\n'.join([line1, line2, line3]).strip()

    def _create_card_operation_message(self, payment_data):
        """
        Форматирует сообщение об операции по карте:
        *200 000* \- Снятие в VB24
        _Карта *4736_
        *5 341 565,78* Остаток
        """

        def escape_md(text):
            if not text:
                return ""
            return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

        amount = escape_md(payment_data.get('amount', '0,00'))
        operation = escape_md(payment_data.get('operation', 'Операция'))
        place = escape_md(payment_data.get('place', ''))
        line1 = f"{amount} \\- {operation}{f' в {place}' if place else ''}"

        card = escape_md(payment_data.get('card', ''))
        line2 = f"_{f'Карта {card}' if card else 'Банковская операция'}_"

        balance = escape_md(payment_data.get('balance', '0,00'))
        line3 = f"*{balance}* Остаток"

        return '\n'.join([line1, line2, line3])

    def _create_incoming_payment_message(self, payment_data):
        """
        Форматирует сообщение о входящем платеже:
        *257 890* \- ФИЛИАЛ ПРИВОЛЖСКИЙ ООО "ДНС РИТЕЙЛ"
        _Оплата по счету № RN254453..._
        *902 755,78* Остаток на счете 40802810802500003196
        """

        def escape_md(text):
            if not text:
                return ""
            return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

        amount = escape_md(payment_data.get('amount', '0,00'))
        sender = escape_md(payment_data.get('sender', 'не указан'))
        line1 = f"{amount} \\- {sender}"

        purpose = escape_md(payment_data.get('purpose', 'не указано'))
        line2 = f"_{purpose}_"

        balance = escape_md(payment_data.get('balance', '0,00'))
        account = escape_md(payment_data.get('account', 'не указан'))
        line3 = f"*{balance}* Остаток на счете {account}"

        return '\n'.join([line1, line2, line3])

    def _create_payment_message(self, payment_data):
        """
        Форматирует сообщение об обычном платеже:
        *160 000* \- ИП Рязанцев Андрей Владимирович
        _Оплата по счету 2 от 24.06.2025 Без НДС_
        *5 541 565,78* Остаток на счете 40802810802500003196
        """

        def escape_md(text):
            if not text:
                return ""
            return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

        amount = escape_md(payment_data.get('amount', '0,00'))
        recipient = escape_md(payment_data.get('recipient', 'не указан'))
        line1 = f"{amount} \\-  {recipient}" if "ИП" in recipient else f"{amount} \\- {recipient}"

        purpose = escape_md(payment_data.get('purpose', 'не указано'))
        line2 = f"_{purpose}_"

        balance = escape_md(payment_data.get('balance', '0,00'))
        account = escape_md(payment_data.get('account', 'не указан'))
        line3 = f"*{balance}* Остаток на счете {account}"

        return '\n'.join([line1, line2, line3])

    def _preprocess_html(self, html):
        """Предварительная обработка HTML"""
        replacements = {
            '\xa0': ' ',
            '\u200b': '',
            '\r\n': '\n',
            '&nbsp;': ' ',
        }
        for old, new in replacements.items():
            html = html.replace(old, new)
        return html

    def _remove_unwanted_elements(self, soup):
        """Удаляет ненужные HTML-элементы"""
        for element in soup(["script", "style", "meta", "link", "head", "title", "noscript"]):
            element.decompose()
        for tag in soup.find_all():
            if not tag.get_text(strip=True):
                tag.decompose()

    def _extract_clean_text(self, soup):
        """Извлекает и очищает текст"""
        text = soup.get_text(separator='\n', strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return '\n'.join(lines)

    def _format_number(self, num_str):
        """Форматирует числовую строку с разделителями тысяч"""
        try:
            num = float(num_str.replace(' ', '').replace(',', '.'))
            return f"{num:,.2f}".replace(',', ' ').replace('.', ',')
        except:
            return num_str

    def _escape_markdown(self, text):
        """Экранирует специальные символы MarkdownV2"""
        if not text:
            return ""
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))