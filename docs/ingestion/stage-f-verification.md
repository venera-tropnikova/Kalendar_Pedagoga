# Этап F — отчёт проверки

Статус: **PASS**.

Флаг остаётся выключенным по умолчанию. Push и deploy не выполнялись.
Монолитный pytest не запускался и остаётся `TEST_ISOLATION_DEFECT`.

## Поведение

При `CANONICAL_INGESTION_ENABLED=false` основной экран по-прежнему открывается
кнопкой «Проверить документы». Модуль маршрута Stage E при этом не импортируется.

При `true`:

- без документа новый экран не запускает прежнюю генерацию;
- VALID подтверждается одним «Да, продолжить» и даёт скачиваемый DOCX;
- семь состояний `YEAR_AMBIGUOUS`, `PLAN_AMBIGUOUS`, `COLUMN_AMBIGUOUS`,
  `HOURS_CONFLICT`, `CONTENT_BOUNDARY_AMBIGUOUS`, `BINDING_AMBIGUOUS`,
  `SOURCE_CONFLICT` показывают только свой вопрос;
- заблокированные состояния и неподтверждённая сессия DOCX не создают;
- подтверждение сохраняется между rerun и сбрасывается при смене источника.

## Проверки

- A–E и новые тесты флага: **323 passed, 1 deselected**, 292.34 с.
  Снятый тест — прежний Word-тест Stage E
  `test_confirmed_route_reaches_docx_without_training_loss`.
- Corpus: одна параметризованная функция на все записи манифеста.
  Готовая ветвь создаёт DOCX через экран; остальные ветви блокируются до файла.
- Визуально, экран флага: desktop 1280 и mobile 390.
  Горизонтального переполнения нет. VALID показывает «Мы нашли… Всё верно?».
  Конфликт часов держит «Да, продолжить» выключенной и не показывает скачивание.
- Process-isolated gate, каталог
  `C:\Users\tropn\AppData\Local\Temp\kp_stage_f_isolated_gate`:
  collected 2219, executed 2218, passed 2079, failed 133, errors 0,
  skipped 6, deselected 1. Обе разности с baseline пустые.
  Baseline SHA-256
  `1e37f7078f5d0744a06358ead2b574fe362b8332deea11dc46a70d6c240b37cc`.
  Прирост passed относительно gate этапа E равен 17 новым тестам.
