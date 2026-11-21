"""只读检查模拟资料和本地合同，不调用模型、Embedding、Chroma或Agent。"""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from dataclasses import replace

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'


def read_jsonl(path):
    with path.open(encoding='utf-8') as file:
        return [json.loads(line) for line in file if line.strip()]


def matches_filter(metadata, condition):
    """本地真值表检查，不能代替Chroma真实过滤验收。"""
    if '$and' in condition:
        return all(matches_filter(metadata, child) for child in condition['$and'])
    if '$or' in condition:
        return any(matches_filter(metadata, child) for child in condition['$or'])
    return all(metadata.get(key) == value for key, value in condition.items())


def expect_error(call, error_type):
    try:
        call()
    except error_type:
        return
    raise AssertionError(f'应抛出{error_type.__name__}')


def main():
    for path in [ROOT / 'starter_姓名.py', Path(__file__)]:
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')
    with (DATA / 'knowledge_manifest.csv').open(encoding='utf-8-sig', newline='') as file:
        manifest = list(csv.DictReader(file))
    assert len(manifest) == 9
    assert len({row['doc_id'] for row in manifest}) == 9
    assert len({row['source'] for row in manifest}) == 9
    documents = 0
    corpus = {}
    type_counts = {}
    for row in manifest:
        path = (DATA / row['source']).resolve()
        assert path.is_relative_to(DATA.resolve()) and path.is_file()
        assert path.suffix[1:] == row['file_type']
        assert row['visibility'] in {'public', 'internal'}
        assert row['department'] in {'all', 'support', 'delivery'}
        assert row['status'] in {'active', 'archived'}
        assert row['doc_kind'] in {'policy', 'faq', 'feedback'}
        assert row['version'].strip()
        text = path.read_text(encoding='utf-8-sig')
        assert text.strip()
        corpus[row['doc_id']] = text
        type_counts[row['file_type']] = type_counts.get(row['file_type'], 0) + 1
        if row['file_type'] == 'csv':
            with path.open(encoding='utf-8-sig', newline='') as file:
                rows = list(csv.DictReader(file))
            assert rows and all(all(value.strip() for value in item.values()) for item in rows)
            documents += len(rows)
        else:
            documents += 1
    assert type_counts == {'md': 5, 'txt': 2, 'csv': 2}
    assert documents == 17
    assert '2个工作小时' in corpus['KB02'] and '4个工作小时' in corpus['KB03']
    assert [row['doc_id'] for row in manifest if row['status'] == 'archived'] == ['KB03']
    assert 'SUP-ESC-17' in corpus['KB06'] and 'DEL-ONLY-42' in corpus['KB07']
    assert all('DEL-ONLY-42' not in text for doc_id, text in corpus.items() if doc_id != 'KB07')
    profiles = json.loads((DATA / 'profiles.json').read_text())
    assert set(profiles) == {'visitor_demo', 'support_demo', 'delivery_demo'}
    assert len({item['principal_id'] for item in profiles.values()}) == 3
    cases = read_jsonl(DATA / 'evaluation_cases.jsonl')
    batch = read_jsonl(DATA / 'batch_questions.jsonl')
    assert [row['case_id'] for row in cases] == [f'C{i:02}' for i in range(1, 23)]
    assert [row['case_id'] for row in batch] == [f'B{i:02}' for i in range(1, 7)]
    for item in cases + batch:
        assert item['profile'] in profiles
    for item in cases:
        assert set(item['expected_doc_ids']) <= set(corpus)
        assert item['mode'] in {'rag', 'agent', 'rag-stream'}
    by_id = {row['case_id']: row for row in cases}
    assert by_id['C04']['session_id'] == by_id['C05']['session_id'] == by_id['C07']['session_id']
    assert by_id['C06']['session_id'] != by_id['C04']['session_id']
    assert by_id['C11']['session_id'] == by_id['C12']['session_id']
    assert by_id['C11']['profile'] != by_id['C12']['profile']
    assert by_id['C08']['session_id'] == by_id['C16']['session_id']
    assert not batch[2]['question'] and all(row['question'] for i, row in enumerate(batch) if i != 2)

    # 以下全部为合成合同样本，不是伪造真实模型或检索运行记录。
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('case_starter', ROOT / 'starter_姓名.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    contexts = {name: module.AccessContext(**item, request_id='local-contract-check')
                for name, item in profiles.items()}
    expected = {
        'visitor_demo': {'KB01', 'KB02', 'KB04', 'KB05', 'KB09'},
        'support_demo': {'KB01', 'KB02', 'KB04', 'KB05', 'KB06', 'KB08', 'KB09'},
        'delivery_demo': {'KB01', 'KB02', 'KB04', 'KB05', 'KB07', 'KB09'},
    }
    for name, context in contexts.items():
        actual = {row['doc_id'] for row in manifest if matches_filter(row, module.build_permission_filter(context))}
        assert actual == expected[name]
    guest = contexts['visitor_demo']
    registry = module.SessionRegistry()
    registry.bind('agent', 'owner-test', contexts['delivery_demo'])
    expect_error(lambda: registry.bind('agent', 'owner-test', guest), PermissionError)
    registry.bind('rag', 'owner-test', guest)  # 两种模式的历史分别管理。
    registry.bind('agent', 'owner-test', replace(contexts['delivery_demo'], request_id='next-turn'))
    sample_id = hashlib.sha256(b'local-contract-fixture').hexdigest()[:24]
    source = module.SourceRef(chunk_id=sample_id, doc_id='KB01', source='knowledge/产品版本与容量.md',
                              version='2026.10', status='active', visibility='public', department='all',
                              request_id=guest.request_id, start_index=0)
    assert len(module.resolve_citations([sample_id, sample_id], [source], guest)) == 1
    expect_error(lambda: module.resolve_citations(['f' * 24], [source], guest), ValueError)
    expect_error(lambda: module.resolve_citations([sample_id], [replace(source, request_id='previous-turn')], guest), ValueError)
    expect_error(lambda: module.resolve_citations([sample_id], [replace(source, status='archived')], guest), PermissionError)
    expect_error(lambda: module.resolve_citations([sample_id], [replace(source, visibility='internal', department='delivery')], guest), PermissionError)
    expect_error(lambda: module.resolve_citations([sample_id], [source, replace(source, source='伪造路径')], guest), ValueError)
    print('PASS：9份资料、17个分块前Document、3种身份、22个顺序场景、6个批量输入。')
    print('PASS：本地权限真值表、会话归属、引用去重、伪造引用、历史来源、归档、越权与来源冲突检查。')
    print('范围：未调用模型、Embedding、Chroma或Agent；这些仍需学员在真实环境完成验收。')


if __name__ == '__main__':
    main()
