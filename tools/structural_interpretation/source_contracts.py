"""Manually reviewed source coordinates, not snapshots of interpreter output.

Paragraph/table/row indices are zero-based in /word/document.xml. SHA-256 is
pinned by the stage A manifest. Totals and explicit row sums are separate:
existing source arithmetic errors MUST remain errors.
"""
SOURCE_CONTRACTS = {
    'key': {
        'classes': ['REFERENCE', 'STUDY_PLAN', 'STUDY_PLAN', 'STUDY_PLAN', 'REFERENCE'],
        'content_bounds': [(1, 241, 310), (2, 419, 474), (3, 589, 652)],
        'plans': [
            (1, 1, [2,3,4,5,6,7,8], 9, {'TOTAL':1,'THEORY':2,'PRACTICE':3}, ['113','35','78'], ['113','35','78']),
            (2, 2, [2,3,4,5,6,7,8], 9, {'TOTAL':1,'THEORY':2,'PRACTICE':3}, ['144','34','110'], ['144','34','110']),
            (3, 3, [2,3,4,5,6,7,8], 9, {'TOTAL':1,'THEORY':2,'PRACTICE':3}, ['144','23','121'], ['144','23','121']),
        ],
    },
    'tour': {
        'classes': ['STUDY_PLAN', 'NORMATIVE_CONTROL', 'NORMATIVE_CONTROL'],
        'content_bounds': [(1, 215, 414)],
        'plans': [(0, 1, [3,15,23,28,33], 38, {'TOTAL':2,'THEORY':3,'PRACTICE':4}, ['72','27','45'], ['72','27','45'])],
    },
    'climb': {
        'classes': ['STUDY_PLAN', 'REFERENCE', 'REFERENCE', 'STUDY_PLAN'],
        'content_bounds': [(1, 171, 473)],
        'plans': [
            (0, 1, [2,3,4,5,6,7], 8, {'TOTAL':2,'THEORY':3,'PRACTICE':4}, ['72','11','61'], ['72','11','61']),
            (3, 1, [1,2,3,4,5,6,7], 8, {'TOTAL':2}, ['72'], ['72']),
        ],
    },
    'nature': {
        'classes': ['REFERENCE', 'REFERENCE', 'NORMATIVE_CONTROL', 'NORMATIVE_CONTROL', 'STUDY_PLAN', 'STUDY_PLAN', 'STUDY_PLAN'],
        'content_bounds': [(1, 110, 136), (2, 136, 158), (3, 158, 182)],
        'plans': [
            (4, 1, [2,3,4,5,6,7,8,9], 10, {'TOTAL':2,'THEORY':3,'PRACTICE':4}, ['96','21','75'], ['96','21','75']),
            (5, 2, [2,3,4,5,6,7,8,9], 10, {'TOTAL':2,'THEORY':3,'PRACTICE':4}, ['128','37','91'], ['130','39','91']),
            (6, 3, [2,3,4,5,6,7,8], 9, {'TOTAL':2,'THEORY':3,'PRACTICE':4}, ['128','32','96'], ['130','34','96']),
        ],
    },
    'orientation': {
        'classes': ['STUDY_PLAN', 'STUDY_PLAN', 'NORMATIVE_CONTROL'],
        'content_bounds': [(1, 204, 256), (2, 434, 481)],
        'plans': [
            (0, 1, [2,9,14], 21, {'TOTAL':2,'THEORY':3,'TRAINING':4,'PRACTICE':5}, ['144','36','36','72'], ['144','36','36','72']),
            (1, 2, [2,9,15], 23, {'TOTAL':2,'THEORY':3,'TRAINING':4,'PRACTICE':5}, ['144','36','36','72'], ['144','36','36','72']),
        ],
    },
}
