#!/usr/bin/env python3
"""Compile the E4B CUDA task with Kubernetes-generated ResourceClaims."""
import argparse
from pathlib import Path
from kfp import compiler, dsl, kubernetes

IMAGE = 'docker.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755'
CLAIMS = {'h1': 'gpu-memory-small', 'h2': 'gpu-memory-large', 'l1': 'gpu-controlled-failure'}


def compile_pipeline(case, output, run_id='dra-book-kubeflow'):
    @dsl.container_component
    def gpu_task(task_id: str, scenario: str, study_id: str):
        return dsl.ContainerSpec(image=IMAGE, command=['python', '-u', '/opt/lab/gpu_workload.py'],
                                 args=['--task-id', task_id, '--scenario', scenario, '--run-id', study_id,
                                       '--control', '/opt/control/control.json', '--hold-timeout', '900'])

    @dsl.pipeline(name='dra-book-kubeflow-' + case)
    def workflow():
        task = gpu_task(task_id='target', scenario='controlled_failure' if case == 'l1' else 'success', study_id=run_id)
        task.set_display_name('gpu-' + case)
        task.set_caching_options(False)
        task.set_cpu_request('1').set_cpu_limit('1')
        task.set_memory_request('2Gi').set_memory_limit('2Gi')
        task.set_retry(num_retries=0, policy='OnFailure', backoff_duration='60s', backoff_factor=1, backoff_max_duration='120s')
        kubernetes.use_config_map_as_volume(task, 'gpu-code', '/opt/lab')
        kubernetes.use_config_map_as_volume(task, 'gpu-control', '/opt/control')
        kubernetes.add_toleration(task, key='nvidia.com/gpu', operator='Exists', effect='NoSchedule')
        for key, value in {'app.kubernetes.io/part-of': 'dra-book', 'app.kubernetes.io/component': 'gpu-' + case,
                           'dra-book/run-id': run_id, 'task-id': 'target'}.items():
            kubernetes.add_pod_label(task, key, value)
        for env, field in [('E4_POD_UID', 'metadata.uid'), ('E4_POD_NAME', 'metadata.name'),
                           ('E4_NAMESPACE', 'metadata.namespace'), ('E4_NODE_NAME', 'spec.nodeName')]:
            kubernetes.use_field_path_as_env(task, env, field)
        kubernetes.set_security_context(task, run_as_user=0, run_as_group=0, run_as_non_root=False)
        task.set_env_variable('PYTORCH_NO_CUDA_MEMORY_CACHING', '1')
        task.set_env_variable('KFP_RETRY_INDEX', '0')
        kubernetes.add_resource_claim(task, CLAIMS[case])

    compiler.Compiler().compile(workflow, str(output))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case', choices=CLAIMS, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--run-id', default='dra-book-kubeflow')
    a = p.parse_args()
    compile_pipeline(a.case, a.output, a.run_id)
