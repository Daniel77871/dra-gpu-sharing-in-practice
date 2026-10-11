"""Extract only named E4/E4B evidence, retaining independent formal verdicts."""
import csv
import hashlib
import io
import json
import re

D4 = 'deliveries/experiment-4-e4-20261011t0035z'
D4B = 'deliveries/experiment-4b-e4b-20261011t060100z'
R4 = 'results/experiment-4/e4-20261011t0035z'
R4B = 'results/experiment-4b/e4b-20261011t060100z'


def fields(value, names):
    return {key: value[key] for key in names.split() if key in value}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class Inputs:
    def __init__(self, reader):
        self.reader = reader
        self.hashes = {}

    def text(self, path):
        raw = self.reader.read_bytes(path)
        self.hashes[path] = {'sha256': hashlib.sha256(raw).hexdigest(),
                             'lines': len(raw.splitlines()), 'bytes': len(raw)}
        self.reader.line_ranges = {key: val['lines'] for key, val in self.hashes.items()}
        return raw.decode('utf-8')

    def json(self, path):
        return json.loads(self.text(path))

    def errors(self, values):
        return [re.sub(r'\b10(?:\.\d{1,3}){3}\b', '<private-ip>',
                       value.replace(str(self.reader.lab), '<source-lab>')) for value in values]


def task(value):
    row = fields(value, 'task_id pod_uid pod_name kfp_task_id kfp_task_name kfp_task_state '
                 'claim_uid claim_name node gpu_uuid exit_code pod_phase task_succeeded '
                 'cuda_execution_verified claim_lifecycle_verified consumed_capacity share_id timing')
    return row


def extract_e4(reader, output):
    inputs = Inputs(reader)
    source = inputs.json(D4 + '/results-summary.json')
    acceptance = fields(source, 'valid status trial_count verified_positive_trials '
                        'verified_negative_trials invalid_trials not_run_trials observed_cuda_trials '
                        'case_acceptance capacity_comparison verified_backend_scopes')
    phase = inputs.json(R4 + '/phase-acceptance.json')
    acceptance['phase_acceptance'] = fields(phase, 'valid current_matrix_passed cleanup_verified '
                                            'accepted_positive_runs accepted_cuda_tasks accepted_negative_runs')
    write(output / 'acceptance.json', acceptance)
    trials = []
    for old in source['trials']:
        raw = inputs.json(R4 + '/' + old['path'] + '/trial.json')
        row = fields(old, 'path stage case mode valid status cuda_execution_verified '
                     'claim_lifecycle_verified negative_assertion_verified pending_proof_verified '
                     'concurrency_proof_verified task_count kfp_run_id kfp_state')
        row.update(fields(raw, 'repeat request task_count attempt pending_proof pins'))
        row['formal_status'] = raw['status']
        row['errors'] = inputs.errors(old['errors'])
        row['tasks'] = [task(value) for value in old['tasks']]
        trials.append(row)
    write(output / 'trials.json', trials)
    write(output / 'task-results.json', source['task_rows'])
    capacity = []
    for value in source['capacity_rows']:
        row = fields(value, 'node gpu_uuid allocation_mode published_memory_bytes reserved_memory_bytes '
                     'remaining_memory_bytes published_cores reserved_cores remaining_cores '
                     'capacity_observed_at snapshot_non_atomic observation_windows '
                     'running_experiment_tasks_on_node pending_experiment_tasks_on_node '
                     'unassigned_pending_experiment_tasks pod_counts_source')
        row['physical'] = fields(value['physical'], 'timestamp memory_total_mib memory_used_mib '
                                 'memory_free_mib capacity_time_delta_seconds') if value.get('physical') else None
        capacity.append(row)
    write(output / 'capacity-samples.json', capacity)
    write(output / 'source-hashes.json', inputs.hashes)


def attempt(value):
    return fields(value, 'valid pod_uid pod_name node kfp_task_id kfp_run_id scenario driver claim_uid '
                  'logical_task_id all_generated_claim_uids gpu_uuid actual_container_exit_code '
                  'intended_workload_exit_codes attempt_index consumed_capacity share_id kfp_pod_link_source')


def extract_e4b(reader, output):
    inputs = Inputs(reader)
    source = inputs.json(D4B + '/results-summary.json')
    write(output / 'acceptance.json', fields(source, 'run_id overall capabilities cases '
          'formal_verified_trials original_invalid_trials_retained raw_verified_with_current_verifier '
          'whole_raw_directory_verifier fresh_venv_used_by_actual_reproduction historical_invalid_records_retained'))
    trials, lifecycle = [], []
    for old in source['trials']:
        path = old['path']
        raw = inputs.json(R4B + '/' + path + '/trial.json')
        row = fields(raw, 'stage case repeat kfp_run_id valid status allocation_verified cuda_verified '
                     'claim_lifecycle_verified cleanup_verified requests setup_holder_requests pins '
                     'started_at submitted_at finished_at pending_started_at pending_pod_uid '
                     'pending_claim_uid pending_duration_seconds release_issued_at requested_memory '
                     'pod_uids claim_uids gpu_uuids nodes original_record_sha256')
        row['path'] = path
        row['offline_status'] = old['verdict']['status']
        row['offline_valid'] = old['verdict']['valid']
        row['offline_errors'] = inputs.errors(old['verdict']['errors'])
        row['attempts'] = [attempt(value) for value in old['verdict']['attempts']]
        trials.append(row)
        if raw['status'] != 'verified':
            continue
        # Named files are linked in the report; no traversal of result trees.
        claim_source = inputs.json(R4B + '/' + path + '/claim-lifecycle.json')
        cleanup = inputs.json(R4B + '/' + path + '/cleanup-verification.json')
        run = inputs.json(R4B + '/' + path + '/pipeline-run.json')
        statuses = inputs.json(R4B + '/' + path + '/pipeline-task-status.json')
        claims = []
        for claim in claim_source['claims']:
            item = fields(claim, 'name uid created_at allocated_observed_at deleted_observed_at '
                          'allocated_timestamp_source created_timestamp_source deletion_timestamp')
            item['owner_uids'] = [owner['uid'] for owner in claim.get('owner_references', [])]
            item['events'] = [fields(version, 'event observed_at') |
                              {'allocated': bool(version.get('allocation'))}
                              for version in claim.get('versions', [])]
            claims.append(item)
        lifecycle.append({'path': path, 'case': raw['case'], 'repeat': raw['repeat'],
            'run': fields(run, 'run_id state created_at finished_at'),
            'tasks': [fields(value, 'pod_uid task_id attempt_index kfp_run_id '
                       'claim_uids claim_created_at allocated_observed_at claim_released_observed_at '
                       'submitted_at pod_scheduled_at container_started_at kfp_terminal_at')
                      for value in claim_source['tasks']],
            'api_tasks': [fields(value, 'name task_id run_id state type') |
                          {'pods': [fields(pod, 'name uid type') for pod in value.get('pods', [])]}
                          for value in statuses],
            'claims': claims, 'cleanup': fields(cleanup, 'natural_lifecycle_observed '
                     'manual_gpu_cleanup at claim_count namespace_still_exists'),
            'claim_name_churn_root_cause': claim_source['claim_name_churn_root_cause'],
            'snapshots_are_atomic': claim_source['snapshots_are_atomic']})
    write(output / 'trials.json', trials)
    write(output / 'lifecycle.json', lifecycle)
    task_rows = inputs.json(D4B + '/task-results.json')
    write(output / 'task-results.json', task_rows)
    # This CSV contains only identities, quantities and timestamps. Preserve all
    # paired sample windows and signed offsets, including invalid formal trials.
    raw_csv = inputs.text(D4B + '/capacity-physical-timeseries.csv')
    rows = list(csv.DictReader(io.StringIO(raw_csv)))
    with (output / 'capacity-samples.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write(output / 'source-hashes.json', inputs.hashes)
