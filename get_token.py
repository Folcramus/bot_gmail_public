import os
from google_auth_oauthlib.flow import Flow


# Конфигурация OAuth 2.0 для Gmail
CLIENT_SECRETS_FILE = 'client_secret.json'  # Файл с credentials из Google Cloud Console
SCOPES = ['https://mail.google.com/']  # Полный доступ к Gmail
REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'  # Специальный URI для desktop-приложений


def get_gmail_refresh_token():
    # Создаем flow для OAuth
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    # Генерируем URL для авторизации
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',  # Гарантирует получение refresh_token
        login_hint=''  # Можно указать email для автозаполнения
    )

    print('Пожалуйста, перейдите по этому URL и авторизуйтесь в Gmail:')
    print(auth_url)
    print('\nПосле авторизации вы получите код, который нужно ввести ниже.')

    # Получаем код авторизации от пользователя
    code = input('Введите код авторизации: ').strip()

    # Получаем токены
    flow.fetch_token(code=code)

    # Получаем credentials
    credentials = flow.credentials

    # Выводим refresh token
    print('\nВаш refresh token для Gmail:')
    print(credentials.refresh_token)

    # Сохраняем токены в файл (рекомендуется)
    save_gmail_tokens(credentials)


def save_gmail_tokens(credentials):
    """Сохраняет токены в файл для последующего использования с Gmail API."""
    tokens = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

    with open('gmail_tokens.json', 'w') as f:
        import json
        json.dump(tokens, f, indent=2)

    print('\nТокены сохранены в файл gmail_tokens.json')


if __name__ == '__main__':
    # Проверяем наличие файла с credentials
    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f'Ошибка: файл {CLIENT_SECRETS_FILE} не найден.')
        print('\nИнструкция по получению client_secret.json:')
        print('1. Перейдите в Google Cloud Console: https://console.cloud.google.com/')
        print('2. Создайте новый проект или выберите существующий')
        print('3. Перейдите в "APIs & Services" > "Library"')
        print('4. Найдите и включите "Gmail API"')
        print('5. Перейдите в "APIs & Services" > "Credentials"')
        print('6. Нажмите "Create Credentials" > "OAuth client ID"')
        print('7. Выберите тип приложения "Desktop app"')
        print('8. Введите название и нажмите "Create"')
        print('9. Скачайте JSON (кнопка "Download") и сохраните как client_secret.json')
        print('10. Убедитесь, что в настройках OAuth разрешен redirect URI: urn:ietf:wg:oauth:2.0:oob')
    else:
        get_gmail_refresh_token()