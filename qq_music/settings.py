"""QQ 音乐点歌：全局/群/用户歌词开关、点歌统计与点歌记录持久化。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger('plugins.qq_music')

_RECORD_CAP = 500
_TOP_SONGS = 20

_DEFAULT = {
    'global': {'show_lyrics': True},
    'stats': {
        'searches': 0,
        'plays': 0,
        'lyrics_sent': 0,
        'lyrics_skipped': 0,
    },
    'song_stats': {},
    'users': {},
    'groups': {},
    'records': [],
}


def _data_path() -> Path:
    try:
        import core.plugin.context as plugin_context

        ctx = plugin_context.ctx
        if ctx and getattr(ctx, 'name', '') == 'qq_music':
            return Path(ctx.get_data_path('settings.json'))
    except Exception:
        pass
    return Path(__file__).resolve().parent / 'data' / 'settings.json'


class MusicSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _data_path()
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(raw, dict):
                    # 兼容旧数据：缺失字段补默认值
                    base = json.loads(json.dumps(_DEFAULT))
                    for key, val in base.items():
                        if key not in raw:
                            raw[key] = val
                        elif isinstance(val, dict) and not isinstance(raw[key], dict):
                            raw[key] = val
                        elif isinstance(val, list) and not isinstance(raw[key], list):
                            raw[key] = val
                    self._data = raw
                    return
            except Exception:
                log.exception('读取点歌设置失败 %s', self.path)
        self._data = json.loads(json.dumps(_DEFAULT))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def _global(self) -> dict[str, Any]:
        g = self._data.get('global')
        if not isinstance(g, dict):
            g = {}
            self._data['global'] = g
        return g

    def _stats(self) -> dict[str, Any]:
        s = self._data.get('stats')
        if not isinstance(s, dict):
            s = {}
            self._data['stats'] = s
        return s

    def _users(self) -> dict[str, Any]:
        u = self._data.get('users')
        if not isinstance(u, dict):
            u = {}
            self._data['users'] = u
        return u

    def _groups(self) -> dict[str, Any]:
        g = self._data.get('groups')
        if not isinstance(g, dict):
            g = {}
            self._data['groups'] = g
        return g

    def _song_stats(self) -> dict[str, Any]:
        s = self._data.get('song_stats')
        if not isinstance(s, dict):
            s = {}
            self._data['song_stats'] = s
        return s

    def _records(self) -> list[dict[str, Any]]:
        r = self._data.get('records')
        if not isinstance(r, list):
            r = []
            self._data['records'] = r
        return r

    def user_key(self, appid: str, user_id: str) -> str:
        return f'{appid}:{user_id}'

    # ---------- 用户 ----------

    def touch_user(self, appid: str, user_id: str, nickname: str = '') -> dict[str, Any]:
        key = self.user_key(appid, user_id)
        users = self._users()
        blob = users.get(key)
        if not isinstance(blob, dict):
            blob = {}
            users[key] = blob
        nick = (nickname or '').strip()
        if nick:
            blob['nickname'] = nick
        blob['last_seen'] = int(time.time())
        return blob

    def set_user_show_lyrics(self, appid: str, user_id: str, value: bool | None, nickname: str = '') -> bool:
        blob = self.touch_user(appid, user_id, nickname)
        blob['show_lyrics'] = value
        self.save()
        if value is None:
            return self.should_show_lyrics(appid, user_id)
        return bool(value)

    def toggle_user_show_lyrics(self, appid: str, user_id: str, nickname: str = '') -> bool:
        new_val = not self.should_show_lyrics(appid, user_id)
        return self.set_user_show_lyrics(appid, user_id, new_val, nickname)

    # ---------- 群 ----------

    def touch_group(self, group_id: str, name: str = '') -> dict[str, Any]:
        gid = str(group_id or '').strip()
        groups = self._groups()
        blob = groups.get(gid)
        if not isinstance(blob, dict):
            blob = {}
            groups[gid] = blob
        if name:
            blob['name'] = str(name).strip()[:60]
        blob['last_seen'] = int(time.time())
        return blob

    def get_group_show_lyrics(self, group_id: str) -> bool | None:
        blob = self._groups().get(str(group_id or '').strip())
        if isinstance(blob, dict) and blob.get('show_lyrics') is not None:
            return bool(blob['show_lyrics'])
        return None

    def set_group_show_lyrics(self, group_id: str, value: bool | None, name: str = '') -> bool:
        blob = self.touch_group(group_id, name)
        blob['show_lyrics'] = value
        self.save()
        return self.should_show_lyrics('', '', group_id)

    def toggle_group_show_lyrics(self, group_id: str, name: str = '') -> bool:
        cur = self.get_group_show_lyrics(group_id)
        if cur is None:
            cur = self.get_global_show_lyrics()
        return self.set_group_show_lyrics(group_id, not cur, name)

    # ---------- 歌词生效优先级：群显式关闭 > 个人 > 群开启 > 全局 ----------

    def should_show_lyrics(self, appid: str, user_id: str, group_id: str = '') -> bool:
        # 群主/管理员显式关闭本群歌词后，群成员个人设置无法再开启（强制关闭）
        if self.get_group_show_lyrics(group_id) is False:
            return False
        key = self.user_key(appid, user_id)
        blob = self._users().get(key)
        if isinstance(blob, dict) and blob.get('show_lyrics') is not None:
            return bool(blob['show_lyrics'])
        gval = self.get_group_show_lyrics(group_id)
        if gval is not None:
            return gval
        return bool(self._global().get('show_lyrics', True))

    def group_lyrics_forced_off(self, group_id: str) -> bool:
        """群主/管理员是否已显式关闭本群歌词（此时普通成员无法自行开启）。"""
        return self.get_group_show_lyrics(group_id) is False

    def get_global_show_lyrics(self) -> bool:
        return bool(self._global().get('show_lyrics', True))

    def set_global_show_lyrics(self, value: bool) -> bool:
        self._global()['show_lyrics'] = bool(value)
        self.save()
        return bool(value)

    # ---------- 统计与记录 ----------

    @staticmethod
    def _inc(container: dict, key: str, amount: int = 1) -> None:
        container[key] = int(container.get(key, 0) or 0) + amount

    def _append_record(self, rec: dict[str, Any]) -> None:
        records = self._records()
        records.append(rec)
        if len(records) > _RECORD_CAP:
            del records[: len(records) - _RECORD_CAP]

    def bump(self, field: str, amount: int = 1) -> None:
        stats = self._stats()
        stats[field] = int(stats.get(field, 0) or 0) + amount
        self.save()

    def record_search(
        self,
        keyword: str = '',
        appid: str = '',
        uid: str = '',
        gid: str = '',
        nickname: str = '',
    ) -> None:
        self._inc(self._stats(), 'searches')
        if appid or uid:
            self._inc(self.touch_user(appid, uid, nickname), 'searches')
        if gid:
            self._inc(self.touch_group(gid), 'searches')
        self._append_record({
            'ts': int(time.time()),
            'type': 'search',
            'appid': appid,
            'uid': uid,
            'gid': gid,
            'nickname': (nickname or '').strip(),
            'keyword': (keyword or '').strip(),
        })
        self.save()

    def record_play(
        self,
        song: str = '',
        singer: str = '',
        keyword: str = '',
        appid: str = '',
        uid: str = '',
        gid: str = '',
        nickname: str = '',
    ) -> None:
        self._inc(self._stats(), 'plays')
        if appid or uid:
            self._inc(self.touch_user(appid, uid, nickname), 'plays')
        if gid:
            self._inc(self.touch_group(gid), 'plays')
        name = (song or '').strip() or '未知'
        singer_txt = (singer or '').strip()
        skey = f'{name}|{singer_txt}'
        stats = self._song_stats()
        if skey not in stats or not isinstance(stats[skey], dict):
            stats[skey] = {'song': name, 'singer': singer_txt, 'count': 0}
        stats[skey]['count'] = int(stats[skey].get('count', 0) or 0) + 1
        self._append_record({
            'ts': int(time.time()),
            'type': 'play',
            'appid': appid,
            'uid': uid,
            'gid': gid,
            'nickname': (nickname or '').strip(),
            'keyword': (keyword or '').strip(),
            'song': name,
            'singer': singer_txt,
        })
        self.save()

    def record_lyric_sent(self) -> None:
        self.bump('lyrics_sent')

    def record_lyric_skipped(self) -> None:
        self.bump('lyrics_skipped')

    def records_view(self, rec_type: str = '', limit: int = 200) -> list[dict[str, Any]]:
        try:
            limit = max(1, min(int(limit or 200), _RECORD_CAP))
        except (TypeError, ValueError):
            limit = 200
        records = self._records()
        if rec_type in ('search', 'play'):
            records = [r for r in records if r.get('type') == rec_type]
        return records[-limit:][::-1]

    def dashboard(self) -> dict[str, Any]:
        users = self._users()
        user_list = []
        for key, blob in users.items():
            if not isinstance(blob, dict):
                continue
            appid, uid = (key.split(':', 1) + [''])[:2]
            override = blob.get('show_lyrics')
            effective = override if override is not None else self.get_global_show_lyrics()
            user_list.append({
                'user_key': key,
                'appid': appid,
                'user_id': uid,
                'nickname': blob.get('nickname') or '',
                'show_lyrics': override,
                'effective_show_lyrics': bool(effective),
                'last_seen': blob.get('last_seen'),
                'searches': int(blob.get('searches', 0) or 0),
                'plays': int(blob.get('plays', 0) or 0),
            })
        user_list.sort(key=lambda x: int(x.get('last_seen') or 0), reverse=True)

        groups = self._groups()
        group_list = []
        for gid, blob in groups.items():
            if not isinstance(blob, dict) or not gid:
                continue
            override = blob.get('show_lyrics')
            effective = override if override is not None else self.get_global_show_lyrics()
            group_list.append({
                'group_id': gid,
                'name': blob.get('name') or '',
                'show_lyrics': override,
                'effective_show_lyrics': bool(effective),
                'last_seen': blob.get('last_seen'),
                'searches': int(blob.get('searches', 0) or 0),
                'plays': int(blob.get('plays', 0) or 0),
            })
        group_list.sort(key=lambda x: int(x.get('last_seen') or 0), reverse=True)

        top = []
        for skey, blob in self._song_stats().items():
            if not isinstance(blob, dict):
                continue
            top.append({
                'song': blob.get('song') or skey,
                'singer': blob.get('singer') or '',
                'count': int(blob.get('count', 0) or 0),
            })
        top.sort(key=lambda x: x['count'], reverse=True)

        return {
            'global': dict(self._global()),
            'stats': dict(self._stats()),
            'users': user_list,
            'groups': group_list,
            'top_songs': top[:_TOP_SONGS],
            'records': self.records_view('', 50),
            'user_count': len(user_list),
            'group_count': len(group_list),
            'data_file': str(self.path),
        }


_store: MusicSettingsStore | None = None


def get_store() -> MusicSettingsStore:
    global _store
    if _store is None:
        _store = MusicSettingsStore()
    return _store
