#!/usr/bin/env python3
"""Summarize raw evidence without equating process survival with isolation."""
import argparse
import csv
import datetime as dt
import json
from pathlib import Path
import re


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def rows(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                result.append(row)
        except ValueError:
            continue  # Original log remains available for inspection.
    return result


def percentile(values, percent):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize(a, b, meta):
    reasons = []
    if not meta.get('complete'):
        reasons.append(meta.get('error', '觀察流程未完成'))
    if meta.get('collection_errors'):
        reasons.append('證據蒐集失敗')
    if meta.get('baseline_observed_seconds', 0) < 60:
        reasons.append('缺少完整 60 秒基準觀察')
    if meta.get('recovery_observed_seconds', 0) < 60:
        reasons.append('缺少完整 60 秒恢復觀察')
    if meta.get('a_restarts') != 0 or meta.get('b_restarts') != 0:
        reasons.append('Pod 重啟資料缺少或發生重啟')
    ready_a = next((e for e in a if e.get('event') == 'A_READY'), {})
    ready_b = next((e for e in b if e.get('event') == 'B_READY'), {})
    expected = meta.get('expected_gpu_uuid')
    if not expected or ready_a.get('gpu_uuid') != expected or ready_b.get('gpu_uuid') != expected:
        reasons.append('GPU UUID 缺少或不一致')
    oom = next((e for e in b if e.get('event') == 'B_OOM'), None)
    if oom is None:
        reasons.append('沒有 B 的 CUDA OOM 證據')
    if meta.get('b_reason') == 'OOMKilled' or meta.get('a_reason') == 'OOMKilled':
        reasons.append('發生系統 RAM OOMKilled')
    if meta.get('b_exit_code') != 1 or not any(e.get('event') == 'B_EXIT' for e in b):
        reasons.append('B 沒有按預期退出')
    if meta.get('a_exit_code') is None:
        reasons.append('A 終止狀態缺少')
    grow = next((e for e in b if e.get('event') == 'B_GROW'), None)
    exited = next((e for e in b if e.get('event') == 'B_EXIT'), None)
    if not grow or not exited or not oom or not ready_b:
        reasons.append('B 階段證據缺少')
    elif not (timestamp(ready_b['utc']) <= timestamp(grow['utc']) <= timestamp(oom['utc']) <= timestamp(exited['utc'])):
        reasons.append('B 階段時間順序錯誤')
    if any(e.get('event') == 'A_TIMEOUT' for e in a):
        reasons.append('A 達到測試保護期限')
    if grow and any(e.get('event') in ('CUDA_ERROR', 'A_INTEGRITY_ERROR', 'A_STOPPED')
                    and timestamp(e['utc']) < timestamp(grow['utc']) for e in a):
        reasons.append('A 在 B 開始前已失敗或停止')
    held = next((e for e in b if e.get('event') == 'B_HOLD'), None)
    if meta.get('mode') == 'hold':
        if held is None:
            reasons.append('保留GPU 記憶體階段缺少')
        else:
            end = next((e for e in b if e.get('event') == 'B_EXIT'), {})
            if end.get('monotonic', 0) - held.get('monotonic', 0) < 29.9:
                reasons.append('保留GPU 記憶體不足 30 秒')
    finish = timestamp(meta['b_finished_utc']) if meta.get('b_finished_utc') else None
    if finish is None:
        reasons.append('B 實際終止時間缺少')
    release = timestamp(exited['utc']) if exited else None
    hold_end = min(finish, release) if finish is not None and release is not None else None
    hold_rows = [e for e in a if held and finish is not None
                 and hold_end is not None and timestamp(held['utc']) <= timestamp(e['utc']) < hold_end
                 and e.get('phase') == 'hold']
    baseline_rows = [e for e in a if e.get('event') == 'A_COMPUTE' and e.get('phase') == 'baseline']
    baseline = [e['latency_ms'] for e in baseline_rows]
    if len(baseline_rows) < 2 or timestamp(baseline_rows[-1]['utc']) - timestamp(baseline_rows[0]['utc']) < 55:
        reasons.append('基準運算樣本不足')
    recovery_rows = [e for e in a if e.get('event') == 'A_COMPUTE' and e.get('phase') == 'recovery']
    if meta.get('a_exit_code') == 0:
        if len(recovery_rows) < 2 or timestamp(recovery_rows[-1]['utc']) - timestamp(recovery_rows[0]['utc']) < 55:
            reasons.append('恢復運算樣本不足')
        if not any(e.get('event') == 'A_STOPPED' for e in a):
            reasons.append('A 正常停止事件缺少')
    during_hold = [e['latency_ms'] for e in hold_rows if e.get('event') == 'A_COMPUTE']
    hold_oom = sum(e.get('event') == 'A_PROBE_OOM' for e in hold_rows) if held else None
    hold_ok = sum(e.get('event') == 'A_PROBE_OK' for e in hold_rows) if held else None
    if meta.get('mode') == 'hold' and meta.get('a_exit_code') == 0 and not (hold_ok or hold_oom):
        reasons.append('保留GPU 記憶體期間沒有 A 配置測試')
    # CRI finishedAt may have second precision. Use the later release marker.
    recovery_anchor = max(finish, release) if finish is not None and release is not None else None
    recovered = [timestamp(e['utc']) - recovery_anchor for e in a
                 if recovery_anchor is not None and e.get('event') == 'A_PROBE_OK' and timestamp(e['utc']) >= recovery_anchor]
    compute_errors = sum(e.get('event') in ('CUDA_ERROR', 'A_INTEGRITY_ERROR') for e in a)
    row = {'mode': meta.get('mode'), 'repeat': meta.get('repeat'),
           'complete': not reasons, 'incomplete_reason': '; '.join(reasons),
           'gpu_uuid': expected, 'b_tensor_mib_at_oom': oom.get('tensor_bytes', 0) / 1024**2 if oom else None,
           'baseline_samples': len(baseline), 'baseline_latency_p95_ms': percentile(baseline, 95),
           'hold_samples': len(during_hold), 'hold_latency_p95_ms': percentile(during_hold, 95),
           'hold_probe_oom': hold_oom, 'hold_probe_ok': hold_ok,
           'allocation_recovery_seconds': min(recovered) if recovered else None,
           'compute_errors': compute_errors,
           'a_exit_code': meta.get('a_exit_code'), 'b_exit_code': meta.get('b_exit_code'),
           }
    row['hold_latency_ratio'] = (row['hold_latency_p95_ms'] / row['baseline_latency_p95_ms']
                                 if row['hold_latency_p95_ms'] is not None and row['baseline_latency_p95_ms'] else None)
    return row


def load_summaries(root):
    summaries = []
    for directory in sorted(Path(root).iterdir()):
        match = re.fullmatch(r'(exit|hold)-r([1-9][0-9]*)', directory.name)
        if not directory.is_dir() or not match:
            continue
        path = directory / 'trial.json'
        meta = json.loads(path.read_text()) if path.exists() else {}
        meta.setdefault('mode', match[1])
        meta.setdefault('repeat', int(match[2]))
        # Final Pod state is independent evidence for exit codes, not a log inference.
        for prefix, role in (('a', 'survivor'), ('b', 'pressure')):
            final = directory / f'{role}-final.json'
            if final.exists():
                pod = json.loads(final.read_text())
                containers = pod.get('status', {}).get('containerStatuses', [])
                state = next((c for c in containers if c.get('name') == 'workload'), {})
                terminated = state.get('state', {}).get('terminated', {})
                meta[prefix + '_exit_code'] = terminated.get('exitCode')
                meta[prefix + '_reason'] = terminated.get('reason')
                meta[prefix + '_restarts'] = state.get('restartCount')
                if prefix == 'b':
                    meta['b_finished_utc'] = terminated.get('finishedAt')
        row = summarize(rows(directory / 'a.jsonl'), rows(directory / 'b.jsonl'), meta)
        gpu = rows(directory / 'gpu.jsonl')
        if not gpu or any('error' in sample or sample.get('gpu_uuid') != meta.get('expected_gpu_uuid')
                          for sample in gpu):
            row['complete'] = False
            row['incomplete_reason'] += ('; ' if row['incomplete_reason'] else '') + 'GPU 取樣缺少或不一致'
        row['trial'] = directory.name
        summaries.append(row)
    return summaries


def book_table(summaries):
    lines = ['| 輪次 | B 張量 MiB | hold 探測 OOM | hold 探測 OK | 恢復秒數 | A 運算錯誤 | A 退出碼 | B 退出碼 | hold p95 倍率 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in summaries:
        def value(key, precision=None):
            number = row[key]
            if number is None:
                return '未測' if row['mode'] == 'exit' and key.startswith('hold_') else '缺少'
            return f'{number:.{precision}f}' if precision is not None else str(number)
        lines.append('| ' + ' | '.join([
            row['trial'], value('b_tensor_mib_at_oom', 0), value('hold_probe_oom'), value('hold_probe_ok'),
            value('allocation_recovery_seconds', 3), value('compute_errors'), value('a_exit_code'),
            value('b_exit_code'), value('hold_latency_ratio', 3)]) + ' |')
    return '\n'.join(lines)


def write_report(root, output=None):
    summaries = load_summaries(root)
    if not summaries:
        raise ValueError('no exit-rN or hold-rN directories found')
    output = Path(output or root)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + '\n')
    with (output / 'summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    lines = ['# 共用 GPU 的 OOM 結果', '', book_table(summaries), '', '未測表示沒有 hold 窗口；缺少表示證據不足。']
    for row in summaries:
        if not row['complete']:
            lines += ['', f"- {row['trial']} 未完成：{row['incomplete_reason']}"]
    (output / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    return summaries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--output', type=Path, help='default: results directory')
    args = parser.parse_args(argv)
    try:
        summaries = write_report(args.results, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f'Analysis failed: {error}\n')
    print(book_table(summaries))
    return 0 if all(row['complete'] for row in summaries) else 2


if __name__ == '__main__':
    raise SystemExit(main())
