# Этап E — отчёт проверки

Статус: **PASS**.

Production UI и обычный pipeline не переключены. `SHADOW_ENABLED = False`.
Push и deploy не выполнялись. Монолитный pytest этого этапа не запускался
и остаётся `TEST_ISOLATION_DEFECT`.

Readiness-gates не менялись. `SOURCE_NOT_MATCHED`, `GENERIC_ONLY` и
`REPEATED_*` не подавлялись.

## Ledger шести групп до исправления

Готовая ветвь корпуса: подтверждённый встроенный план, год 1, 36 недель,
144 часа (теория 36, учебно-тренировочные 36, практика 72).

Все шесть групп — механизм B. У каждого слота `title_fallback`, нет
идентификаторов фрагментов, нормализованный source совпадает с заголовком
строки УТП, различающих atoms нет. SentenceFrame до исправления был общим
title-кадром (`title_derived=False`, `proven=False`): теория — «характеризует
содержание темы …», практика — «выполняет практическую работу по теме …».
RESULT у всех был «Подготавливает.», CONTROL — «проверка подготовки».

| Группа | Тема | Год | Недели | Тип | Canonical ID | SourceSpan |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 3.4 Общая физическая подготовка | 1 | 18–19 | теоретическое занятие | `EMBEDDED:…:row:18:0` | `/word/document.xml` `(0, 74, 20, 2, 1)` |
| 2 | 3.4 Общая физическая подготовка | 1 | 20–23 | практикум | тот же | тот же |
| 3 | 3.5 Специальная физическая подготовка | 1 | 25–26 | теоретическое занятие | `EMBEDDED:…:row:19:0` | `/word/document.xml` `(0, 74, 21, 2, 1)` |
| 4 | 3.5 Специальная физическая подготовка | 1 | 27–29 | практикум | тот же | тот же |
| 5 | 3.6 Лыжная подготовка | 1 | 31–32 | теоретическое занятие | `EMBEDDED:…:row:20:0` | `/word/document.xml` `(0, 74, 22, 2, 1)` |
| 6 | 3.6 Лыжная подготовка | 1 | 33–36 | практикум | тот же | тот же |

Полный префикс идентификатора:
`EMBEDDED:be1a3ac2688d808f785cfe2edd1beedefa97f3004464a289999daf97a2070cbd`.

Семантических различий между слотами одной группы не было, поэтому
механизм A (`SEMANTIC_COLLAPSE`) к ним не применялся.

## После существующего фазового кадра

RESULT и CONTROL собраны из `stage_title_frame`, без суффикса после генерации.

- 3.4 теория, недели 18–19: этап 1 из 2 и этап 2 из 2.
- 3.4 практика, недели 20–23: этап 1–4 из 4.
- 3.5 теория, недели 25–26: этап 1 из 2 и этап 2 из 2. Неделя 24 сохраняет
  отдельный grounded source и в эту фазовую группу не входит.
- 3.5 практика, недели 27–29: этап 1–3 из 4. Четвёртый кандидат той же темы
  и канала — title-only часть смешанной недели 30; её итоговый RESULT остаётся
  отдельным многоисточниковым текстом, потому что базовый кадр не title-derived.
- 3.6 теория, недели 31–32: этап 1 из 2 и этап 2 из 2.
- 3.6 практика, недели 33–36: этап 1–4 из 4.

## Готовая ветвь

- `SOURCE_NOT_MATCHED` = 0
- `GENERIC_ONLY` = 0
- `REPEATED_SCHEDULE_RESULT_CONTROL` = 0
- недели = 36, часы = 144, теория 36, учебно-тренировочные 36, практика 72

Неподтверждённые ветви корпуса DOCX не создают.

Фактический shadow DOCX:
`C:\Users\tropn\AppData\Local\Temp\kp_stage_e_shadow_docx\orientation-year1-shadow.docx`
(23421 байт). Структурный и визуальный QA всех страниц выполнен
`validate_calendar_docx` и `validate_calendar_docx_visual` внутри `run_shadow`;
блокирующих замечаний нет. Учебный год сетки — переданный `2026–2027`.

## Проверки

1. Non-Word набор `tests/lossless_document`, `tests/structural_interpretation`,
   `tests/ingestion_confirmation`, `tests/ingestion_adapter`,
   `tests/ingestion_shadow` без
   `test_confirmed_route_reaches_docx_without_training_loss`:
   **306 passed, 1 deselected**, 213.88 с.
2. Счётчики готовой ветви — в этом же прогоне, `publish=False`.
3. `test_confirmed_route_reaches_docx_without_training_loss`: **1 passed**, 32.84 с.
4. Shadow DOCX готовой ветви и QA всех страниц — см. выше.
5. Process-isolated gate, новый каталог
   `C:\Users\tropn\AppData\Local\Temp\kp_stage_e_isolated_gate_e2`:
   collected 2202, executed 2201, passed 2062, failed 133, errors 0,
   skipped 6, deselected 1. `current FAILED − baseline FAILED` пусто,
   `baseline FAILED − current FAILED` пусто. Baseline SHA-256
   `1e37f7078f5d0744a06358ead2b574fe362b8332deea11dc46a70d6c240b37cc`.
   Прирост passed относительно прежнего isolated gate равен 15 новым тестам shadow.

Универсальные тесты повторов: разные sources, сведённые в один кадр
(`SEMANTIC_COLLAPSE`); одинаковый source на несколько слотов; title-only
multiweek; каталог без повторного использования; запрет смешения тем;
детерминированный повторный запуск. Запрет смешения годов и тем на уровне
привязки слотов сохранён в `test_slot_binding.py`.
