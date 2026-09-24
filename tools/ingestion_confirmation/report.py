"""Assemble the common corpus evidence packet; no document-specific rules."""
import json
from pathlib import Path


def render_report(directory, manifest):
    records = json.loads(Path(manifest).read_text(encoding='utf-8-sig'))
    packets = [json.loads((Path(directory) / (record['id'] + '.json')).read_text(encoding='utf-8'))
               for record in records]
    lines = ['# Этап C: общий протокол corpus', '',
             'Одна функция теста, одна машина состояний, один Streamlit renderer. '
             'Неподтверждённые связи и исходные ошибки не исправлялись автоматически.', '',
             '| Документ | SHA-256 | Блоки A / B | Разделы / фрагменты | Начальное состояние | Потери / дубликаты |',
             '|---|---|---:|---:|---|---:|']
    for packet in packets:
        assert packet['losses'] == packet['duplicates'] == 0 and packet['shared_ui_pass']
        assert packet['blocks_a'] == packet['blocks_b']
        lines.append(f"| {packet['label']} | `{packet['sha256']}` | {packet['blocks_a']} / {packet['blocks_b']} | "
                     f"{packet['sections']} / {packet['fragments']} | {packet['initial_state']} | 0 / 0 |")
    lines += ['', '## Все исследованные ветви', '']
    for packet in packets:
        lines += ['### ' + packet['label'], '']
        for i, branch in enumerate(packet['branches'], 1):
            lines.append(f"- Ветвь {i}: год {branch['year']}; `{branch['state']}`; "
                         f"подтверждений выбора {branch['confirmed_events']}; спорных связей {branch['pending_bindings']}.")
            if branch['plan']:
                lines.append('  План: `' + branch['plan'] + '`.')
            for issue in branch['hour_issues']:
                lines.append(f"  Часы: {issue['label']}; документ={issue['declared']}; "
                             f"расчёт={issue['calculated']}; разница={issue['difference']}.")
        lines += ['']
    lines += ['`None` в числах означает невозможность точного расчёта по исходным значениям; '
              'это блокировка, а не ноль.', '',
              'PASS относится к инвариантам машины подтверждения и сохранению данных. '
              'Это не пользовательское подтверждение спорных связей и не допуск документов в downstream.']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    directory = Path('_shadow_out/confirmation_stage_c')
    (directory / 'report.md').write_text(render_report(directory, 'tests/lossless_document/corpus.json'), encoding='utf-8')
