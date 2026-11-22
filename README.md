# Расписание уроков — Школа русской культуры

Автоматическое обновление расписания из группы **MAX** (`https://web.max.ru/-69393426093561`), веб-сайт на **GitHub Pages** и **Telegram-бот**.

## Что осталось сделать в GitHub (с телефона за 2 минуты)

### 1. Создайте файл `.github/workflows/update.yml`
GitHub запрещает внешним ИИ-интеграциям напрямую создавать файлы в папке `.github/workflows/`.
1. В репозитории нажмите **Add file** → **Create new file**.
2. В поле имени файла введите: `.github/workflows/update.yml`
3. Вставьте код:

```yaml
name: Update Schedule

on:
  schedule:
    - cron: '*/30 * * * *'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  parse:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install playwright pdfplumber
          playwright install chromium
      - name: Run parser
        env:
          MAX_AUTH: ${{ secrets.MAX_AUTH }}
          MAX_DEVICE_ID: ${{ secrets.MAX_DEVICE_ID }}
        run: python parser.py
      - name: Commit & Push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add schedule.json latest.pdf
          git diff --staged --quiet || git commit -m "Update schedule"
          git push
```

### 2. Добавьте секреты
Откройте **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
- `MAX_AUTH` — полностью скопированное значение ключа `__oneme_auth` из хранилища Firefox (`{"viewerId":...,"token":"..."}`).
- `MAX_DEVICE_ID` — значение ключа `__oneme_device_id` (`98a01cf7-de09-4399-a96e-2edd39a65e49`).

### 3. Включите GitHub Pages
Откройте **Settings** → **Pages** → **Source: Deploy from a branch** → ветка `main` (`/root`) → **Save**.
Сайт заработает по ссылке: `https://wwteamw.github.io/school-schedule/`

### 4. Первый тестовый запуск
Перейдите во вкладку **Actions** → **Update Schedule** → **Run workflow**.
