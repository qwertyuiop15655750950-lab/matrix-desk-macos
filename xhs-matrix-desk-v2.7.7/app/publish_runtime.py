"""发布任务互斥及单账号浏览器回收。登录目录始终保留。"""
import threading


def start(api, target, *args):
    # Reserve before starting the thread, so repeated button clicks cannot race.
    with api._publish_state_lock:
        if api._publish_busy:
            return api._err('正在逐个发布，请等待当前任务结束；可暂停队列')
        api._publish_busy = True

    def run():
        try:
            target(*args)
        finally:
            api.close_thread_conn()
            with api._publish_state_lock:
                api._publish_busy = False
    try:
        threading.Thread(target=run, daemon=True, name='serial-publish').start()
    except Exception:
        with api._publish_state_lock:
            api._publish_busy = False
        raise
    return api._ok(msg='开始逐个发布，完成一个账号后释放浏览器…')


def release_account_window(api, aid):
    """Release only this account's idle window before opening its saved profile.

    Do not remove a still-running actor from the registry. A queued stop signal
    alone is not proof its Chromium process has released the profile lock.
    """
    actor = api._get_actor(aid)
    if actor is None:
        return
    if actor.heartbeat().get('current_task'):
        raise ValueError('该账号浏览器正在执行其他任务，本次发布已跳过')
    actor.stop()
    if actor.is_alive():
        raise ValueError('该账号浏览器尚未关闭，本次发布已跳过，请稍后重试')
    with api._actors_lock:
        if api._actors.get(int(aid)) is actor:
            api._actors.pop(int(aid), None)


def publish_one(api, callback, pw, conn, draft, did, aid, seed):
    key = ('xiaohongshu', int(aid))
    reservation = {'type': 'publish'}
    with api._browser_jobs_lock:
        if key in api._browser_jobs:
            raise ValueError('该账号正在采集或执行浏览器任务，本次发布已跳过')
        api._browser_jobs[key] = reservation
    try:
        release_account_window(api, aid)
        return callback(pw, conn, draft, did, aid, seed)
    finally:
        with api._browser_jobs_lock:
            if api._browser_jobs.get(key) is reservation:
                api._browser_jobs.pop(key)
