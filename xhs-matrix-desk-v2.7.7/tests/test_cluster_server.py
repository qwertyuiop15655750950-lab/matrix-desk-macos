import json
import unittest
import urllib.request
from app.cluster import ClusterState
from app.cluster_server import start_cluster_server

class ClusterServerTests(unittest.TestCase):
    def test_status_endpoint_with_logging_callback(self):
        logs=[]
        server,thread=start_cluster_server(ClusterState(),port=0,log_fn=lambda text: logs.append(text))
        try:
            self.assertEqual(server.server_address[0], '127.0.0.1')
            url=f'http://127.0.0.1:{server.server_address[1]}/api/cluster/status'
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=5) as response:
                result=json.load(response)
            self.assertTrue(result['ok'])
            self.assertEqual(result['workers'],[])
            self.assertTrue(logs)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def request(self, server, path, data=None):
        url = f'http://127.0.0.1:{server.server_address[1]}{path}'
        request = urllib.request.Request(url,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            return json.load(response)

    def test_two_servers_keep_worker_tasks_results_and_logs_separate(self):
        first, second = ClusterState(), ClusterState()
        logs_first, logs_second = [], []
        server_first, thread_first = start_cluster_server(first, port=0, log_fn=logs_first.append)
        server_second, thread_second = start_cluster_server(second, port=0, log_fn=logs_second.append)
        try:
            self.request(server_first, '/api/worker/heartbeat', {'name': 'first-worker', 'accounts': 3, 'load': 1})
            self.assertEqual(len(logs_first), 1)
            self.assertEqual(logs_second, [])
            self.request(server_second, '/api/worker/heartbeat', {'name': 'second-worker', 'accounts': 5, 'load': 2})
            self.assertEqual([w['name'] for w in self.request(server_first, '/api/cluster/status')['workers']], ['first-worker'])
            self.assertEqual([w['name'] for w in self.request(server_second, '/api/cluster/status')['workers']], ['second-worker'])
            task_first = first.push_task('shared-worker', 'scan', {'source': 'first'})
            task_second = second.push_task('shared-worker', 'scan', {'source': 'second'})
            # Both states use t1, so dispatch/result routing must depend on the
            # listening server rather than the last registered handler class.
            self.assertEqual(task_first, task_second)
            tasks_first = self.request(server_first, '/api/worker/tasks?name=shared-worker')['tasks']
            tasks_second = self.request(server_second, '/api/worker/tasks?name=shared-worker')['tasks']
            self.assertEqual(tasks_first[0]['payload'], {'source': 'first'})
            self.assertEqual(tasks_second[0]['payload'], {'source': 'second'})
            self.request(server_first, '/api/worker/result', {'task_id': task_first, 'status': 'done', 'data': {'result': 'first'}})
            self.assertEqual(first.task_status(task_first)['result'], {'result': 'first'})
            self.assertIsNone(second.task_status(task_second)['result'])
            self.request(server_second, '/api/worker/result', {'task_id': task_second, 'status': 'failed', 'data': {'result': 'second'}})
            self.assertEqual(second.task_status(task_second)['result'], {'result': 'second'})
            self.assertEqual(first.task_status(task_first)['status'], 'done')
            self.assertEqual(len(logs_first), 4)
            self.assertEqual(len(logs_second), 4)
        finally:
            for server, thread in [(server_first, thread_first), (server_second, thread_second)]:
                server.shutdown(); server.server_close(); thread.join(2)

    def test_failed_second_bind_does_not_redirect_existing_server(self):
        state, logs = ClusterState(), []
        state.heartbeat('original-worker', 1, 0)
        server, thread = start_cluster_server(state, port=0, log_fn=logs.append)
        other_logs = []
        try:
            with self.assertRaises(OSError):
                start_cluster_server(ClusterState(), port=server.server_address[1], log_fn=other_logs.append)
            status = self.request(server, '/api/cluster/status')
            self.assertEqual([worker['name'] for worker in status['workers']], ['original-worker'])
            self.assertEqual(len(logs), 1)
            self.assertEqual(other_logs, [])
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

if __name__=='__main__': unittest.main()
