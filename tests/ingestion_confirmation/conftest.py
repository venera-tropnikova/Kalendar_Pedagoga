"""Generated documents, shared helpers and common corpus traversal for stage C."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('stage_b_generators', Path(__file__).parents[1] /
                                            'structural_interpretation/test_structural_properties.py')
generators = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generators)
p, table, interpret = generators.p, generators.table, generators.interpret


def structure(year=1, order=tuple(range(6)), depth=1, merged=False, count=1, hours_delta=0,
              boundary=True, text='Описание упражнения.', label='Тема Альфа'):
    values = [('1', label, '2', '0,5', '0.5', '1'),
              ('2', 'Тема Бета', '2', '1/2', '0,5', '1'),
              ('', 'Итого', str(4 + hours_delta), '1', '1', '2')]
    body = p(f'Учебно-тематический план {year} года обучения', bold=True)
    body += table(order=order, header_depth=depth, combined=merged, values=values) * count
    if boundary:
        body += p(f'Содержание программы {year} года обучения', bold=True)
    body += p('1. ' + label, bold=True) + p(text)
    body += p('2. Тема Бета', bold=True) + p('Ещё одно упражнение.')
    return interpret(body)


@pytest.fixture
def clean_structure():
    return structure()
