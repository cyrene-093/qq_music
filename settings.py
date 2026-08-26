"""QQ 音乐点歌：全局/群/用户歌词开关、点歌统计与点歌记录持久化。"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger('plugins.qq_music')

_RECORD_CAP = 500
_TOP_SONGS = 20

_DEFAULT = {
    'global': {
        'show_lyrics': True,
        'music_source': 'aa_qq',
        'custom_source_url': '',
        'qq_cookie': '',
    },
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
                        elif isinstance(val, dict) and isinstance(raw.get(key), dict):
                            for k2, v2 in val.items():
                                if k2 not in raw[key]:
                                    raw[key][k2] = v2
                        elif isinstance(val, dict) and not isinstance(raw.get(key), dict):
                            raw[key] = val
                        elif isinstance(val, list) and not isinstance(raw.get(key), list):
                            raw[key] = val
                    self._data = raw
                    self._migrate_default_source()
                    self._migrate_clear_cookie()
                    return
            except Exception:
                log.exception('读取点歌设置失败 %s', self.path)
        self._data = json.loads(json.dumps(_DEFAULT))

    def _migrate_default_source(self) -> None:
        g = self._global()
        if g.get('_opensource_default_applied'):
            return
        sid = str(g.get('music_source') or '').strip()
        # 开源默认：第三方搜播一体；不再把用户强制迁到官方搜
        if sid in ('', 'official_qq') and not g.get('_keep_official_source'):
            g['music_source'] = 'aa_qq'
        g['_opensource_default_applied'] = True
        g.pop('_official_default_applied', None)
        try:
            self.save()
        except Exception:
            log.debug('迁移默认歌源失败', exc_info=True)

    def _migrate_clear_cookie(self) -> None:
        """面板已移除 Cookie 配置，清空本地票据避免误用与误传。"""
        g = self._global()
        if g.get('_cookie_ui_removed'):
            return
        g['qq_cookie'] = ''
        g['_cookie_ui_removed'] = True
        try:
            self.save()
        except Exception:
            log.debug('清理 Cookie 字段失败', exc_info=True)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._data, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(self.path.suffix + '.tmp')
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, self.path)

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

    def get_custom_source_url(self) -> str:
        return str(self._global().get('custom_source_url') or '').strip()

    def set_custom_source_url(self, url: str) -> str:
        val = str(url or '').strip()
        self._global()['custom_source_url'] = val
        self.save()
        return val

    def get_qq_cookie(self) -> str:
        from plugins.qq_music.official_qq import parse_cookie

        return parse_cookie(str(self._global().get('qq_cookie') or ''))

    def set_qq_cookie(self, raw: str) -> str:
        from plugins.qq_music.official_qq import parse_cookie

        val = parse_cookie(raw)
        self._global()['qq_cookie'] = val
        try:
            from plugins.qq_music.official_qq import set_play_restricted

            set_play_restricted(False)
        except Exception:
            pass
        self.save()
        return val

    def cookie_status(self) -> dict[str, Any]:
        from plugins.qq_music.official_qq import cookie_mask, cookie_music_key

        raw = self.get_qq_cookie()
        return {
            'has_cookie': bool(raw),
            'has_ticket': bool(cookie_music_key(raw)) if raw else False,
            'uin_mask': cookie_mask(raw) if raw else '',
        }

    def get_music_source(self, group_id: str = '') -> str:
        gid = str(group_id or '').strip()
        if gid:
            blob = self._groups().get(gid)
            if isinstance(blob, dict):
                sid = str(blob.get('music_source') or '').strip()
                if sid:
                    return sid
        sid = str(self._global().get('music_source') or '').strip()
        return sid or 'aa_qq'

    def get_group_music_source(self, group_id: str) -> str | None:
        blob = self._groups().get(str(group_id or '').strip())
        if isinstance(blob, dict):
            sid = str(blob.get('music_source') or '').strip()
            if sid:
                return sid
        return None

    def set_music_source(self, source_id: str, group_id: str = '') -> str:
        from plugins.qq_music.sources import get_source, known_source_ids

        sid = str(source_id or '').strip() or 'aa_qq'
        gid = str(group_id or '').strip()
        if gid:
            if sid in ('', 'follow', 'global', '跟随', '跟随全局'):
                blob = self.touch_group(gid)
                blob.pop('music_source', None)
                self.save()
                return self.get_music_source('')
            if sid not in known_source_ids(self.get_custom_source_url()):
                sid = get_source(sid, self.get_custom_source_url()).id
            self.touch_group(gid)['music_source'] = sid
            self.save()
            return sid
        if sid not in known_source_ids(self.get_custom_source_url()):
            sid = get_source(sid, self.get_custom_source_url()).id
        self._global()['music_source'] = sid
        self.save()
        return sid

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
        scope: str = 'personal',
    ) -> None:
        scope = 'group' if scope == 'group' else 'personal'
        self._inc(self._stats(), 'searches')
        if appid or uid:
            self._inc(self.touch_user(appid, uid, nickname), 'searches')
        if gid:
            self._inc(self.touch_group(gid), 'searches')
        self._append_record({
            'ts': int(time.time()),
            'type': 'search',
            'scope': scope,
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
        cover: str = '',
        url: str = '',
        scope: str = 'personal',
    ) -> None:
        scope = 'group' if scope == 'group' else 'personal'
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
        if cover and not stats[skey].get('cover'):
            stats[skey]['cover'] = cover
        if url and not stats[skey].get('url'):
            stats[skey]['url'] = url
        stats[skey]['count'] = int(stats[skey].get('count', 0) or 0) + 1
        self._append_record({
            'ts': int(time.time()),
            'type': 'play',
            'scope': scope,
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
        out: list[dict[str, Any]] = []
        for rec in records[-limit:][::-1]:
            if not isinstance(rec, dict):
                continue
            item = dict(rec)
            item['scope'] = 'group' if item.get('scope') == 'group' else 'personal'
            out.append(item)
        return out

    @staticmethod
    def _qq_group_avatar(qq: str) -> str:
        q = str(qq or '').strip()
        if not q or not q.isdigit():
            return ''
        return f'https://p.qlogo.cn/gh/{q}/{q}/100'

    @staticmethod
    def _qq_group_avatar_alt(qq: str) -> str:
        q = str(qq or '').strip()
        if not q or not q.isdigit():
            return ''
        return f'http://q1.qlogo.cn/g?b=qq&nk={q}&s=100'

    @staticmethod
    def _avatar_from_api(info: dict | None) -> str:
        if not isinstance(info, dict):
            return ''
        for key in (
            'group_face_url', 'avatar_url', 'avatar', 'face_url',
            'group_avatar', 'group_face', 'face',
        ):
            val = str(info.get(key) or '').strip()
            if val.startswith('http'):
                return val
        return ''

    @staticmethod
    def _member_count_from_users(users_raw) -> int:
        try:
            users = json.loads(users_raw) if isinstance(users_raw, str) else users_raw
            if isinstance(users, list):
                return len(users)
        except Exception:
            pass
        return 0

    def _group_meta_map(self) -> dict[str, dict[str, Any]]:
        """从框架 data.db 与群备注读取群人数、头像（备注群号 → QQ 头像）。"""
        meta: dict[str, dict[str, Any]] = {}
        try:
            from core.bot.manager import _bot_manager_ref

            mgr = _bot_manager_ref
            if mgr and getattr(mgr, '_bots', None):
                for bot in mgr._bots.values():
                    ls = getattr(bot, 'log_service', None)
                    if not ls:
                        continue
                    rows = ls.query_data(
                        'SELECT group_id, group_member_num, users, group_name FROM groups_users'
                    ) or []
                    for row in rows:
                        gid = str(row.get('group_id') or '').strip()
                        if not gid:
                            continue
                        cnt = int(row.get('group_member_num') or 0)
                        if cnt <= 0:
                            cnt = self._member_count_from_users(row.get('users'))
                        cur = meta.get(gid, {})
                        if cnt >= int(cur.get('member_count', 0) or 0):
                            cur['member_count'] = cnt
                        # 群名称仅用于内部匹配备注，前端不展示
                        gname = str(row.get('group_name') or '').strip()
                        if gname:
                            cur['group_name'] = gname
                            if not str(cur.get('name') or '').strip():
                                cur['name'] = gname
                        meta[gid] = cur
            base = getattr(mgr, '_base_dir', '') if mgr else ''
            remarks_path = os.path.join(base, 'data', 'group_remarks.json')
            remark_by_openid: dict[str, dict[str, str]] = {}
            if base and os.path.isfile(remarks_path):
                with open(remarks_path, encoding='utf-8') as f:
                    remarks = json.load(f)
                if isinstance(remarks, dict):
                    for key, val in remarks.items():
                        key = str(key).strip()
                        if not key:
                            continue
                        qq = ''
                        name = ''
                        if isinstance(val, dict):
                            qq = str(val.get('qq') or '').strip()
                            name = str(val.get('name') or '').strip()
                        elif isinstance(val, str):
                            name = val.strip()
                        remark_by_openid[key] = {'qq': qq, 'name': name}
            for gid, blob in meta.items():
                rem = remark_by_openid.get(gid, {})
                qq = rem.get('qq', '')
                rname = rem.get('name', '')
                if rname and not str(blob.get('name') or '').strip():
                    blob['name'] = rname
                if qq:
                    blob['group_qq'] = qq
                    avatar = self._qq_group_avatar(qq) or self._qq_group_avatar_alt(qq)
                    if avatar:
                        blob['avatar'] = avatar
            # 备注里可能用群号作 key，尝试与 openid 群记录关联
            for key, rem in remark_by_openid.items():
                qq = rem.get('qq', '')
                if not qq:
                    continue
                avatar = self._qq_group_avatar(qq) or self._qq_group_avatar_alt(qq)
                if not avatar:
                    continue
                if key in meta:
                    if not meta[key].get('avatar'):
                        meta[key]['avatar'] = avatar
                    if not meta[key].get('group_qq'):
                        meta[key]['group_qq'] = qq
                    rname = rem.get('name', '')
                    if rname and not str(meta[key].get('name') or '').strip():
                        meta[key]['name'] = rname
                else:
                    meta[key] = {'avatar': avatar, 'group_qq': qq}
                    rname = rem.get('name', '')
                    if rname:
                        meta[key]['name'] = rname
        except Exception:
            log.exception('读取群元数据失败')
        return meta

    def _apply_group_meta_to_blob(self, gid: str, blob: dict[str, Any], extra: dict[str, Any]) -> bool:
        changed = False
        mc = int(extra.get('member_count') or 0)
        if mc > 0 and int(blob.get('member_count') or 0) != mc:
            blob['member_count'] = mc
            changed = True
        av = str(extra.get('avatar') or '').strip()
        if av and str(blob.get('avatar') or '') != av:
            blob['avatar'] = av
            changed = True
        qq = str(extra.get('group_qq') or '').strip()
        if qq and str(blob.get('group_qq') or '') != qq:
            blob['group_qq'] = qq
            changed = True
        gname = str(extra.get('name') or extra.get('group_name') or '').strip()
        if gname and str(blob.get('name') or '') != gname:
            blob['name'] = gname
            changed = True
        return changed

    async def sync_groups_meta(self, max_api: int = 24) -> None:
        """补齐群人数/头像：data.db → 备注头像 → 平台 API。"""
        meta = self._group_meta_map()
        groups = self._groups()
        changed = False
        for gid, blob in groups.items():
            if not isinstance(blob, dict) or not gid:
                continue
            if self._apply_group_meta_to_blob(gid, blob, meta.get(gid, {})):
                changed = True

        need_api = [
            gid for gid, blob in groups.items()
            if isinstance(blob, dict) and gid
            and (int(blob.get('member_count') or 0) <= 0 or not str(blob.get('avatar') or '').strip())
        ]
        if need_api:
            if await self._refresh_groups_from_api(need_api[:max_api]):
                changed = True
            # API 后再次合并 DB/备注（可能已写入 group_member_num）
            meta = self._group_meta_map()
            for gid in need_api[:max_api]:
                blob = groups.get(gid)
                if isinstance(blob, dict) and self._apply_group_meta_to_blob(gid, blob, meta.get(gid, {})):
                    changed = True

        if changed:
            self.save()

    async def _refresh_groups_from_api(self, gids: list[str]) -> bool:
        changed = False
        try:
            from core.bot.manager import _bot_manager_ref

            mgr = _bot_manager_ref
            if not mgr or not getattr(mgr, '_bots', None):
                return False
            bot = next(iter(mgr._bots.values()))
            sender = bot.sender
            groups = self._groups()
            for gid in gids:
                blob = groups.get(gid)
                if not isinstance(blob, dict):
                    continue
                try:
                    info = await sender.get_group_info(gid)
                    if isinstance(info, dict):
                        cnt = int(info.get('group_member_num') or 0)
                        if cnt > 0 and int(blob.get('member_count') or 0) != cnt:
                            blob['member_count'] = cnt
                            changed = True
                        av = self._avatar_from_api(info)
                        if av and str(blob.get('avatar') or '') != av:
                            blob['avatar'] = av
                            changed = True
                        gname = str(
                            info.get('group_name') or info.get('name') or ''
                        ).strip()
                        if gname and str(blob.get('name') or '') != gname:
                            blob['name'] = gname
                            changed = True
                        gqq = str(
                            info.get('group_id') or info.get('group_no') or ''
                        ).strip()
                        if gqq.isdigit():
                            if not str(blob.get('group_qq') or '').strip():
                                blob['group_qq'] = gqq
                                changed = True
                            if not str(blob.get('avatar') or '').strip():
                                av2 = (
                                    self._qq_group_avatar(gqq)
                                    or self._qq_group_avatar_alt(gqq)
                                )
                                if av2:
                                    blob['avatar'] = av2
                                    changed = True
                except Exception:
                    log.debug('get_group_info 失败 %s', gid)
                if int(blob.get('member_count') or 0) <= 0:
                    try:
                        record = await sender.get_group_record(gid)
                        if isinstance(record, dict):
                            av = self._avatar_from_api(record)
                            if av and str(blob.get('avatar') or '') != av:
                                blob['avatar'] = av
                                changed = True
                            cnt = int(record.get('group_member_num') or 0)
                            if cnt <= 0:
                                cnt = len(record.get('users') or [])
                            if cnt > 0:
                                blob['member_count'] = cnt
                                changed = True
                    except Exception:
                        log.debug('get_group_record 失败 %s', gid)
        except Exception:
            log.exception('API 刷新群元数据失败')
        return changed

    def _resolve_group_member_count(self, blob: dict[str, Any], extra: dict[str, Any]) -> int | None:
        for src in (blob, extra):
            cnt = int(src.get('member_count') or 0)
            if cnt > 0:
                return cnt
        return None

    @staticmethod
    def _resolve_group_name(blob: dict[str, Any], extra: dict[str, Any]) -> str:
        for src in (blob, extra):
            for key in ('name', 'group_name'):
                n = str(src.get(key) or '').strip()
                if n:
                    return n
        return ''

    def _remarks_path(self) -> str:
        try:
            from core.bot.manager import _bot_manager_ref

            mgr = _bot_manager_ref
            base = getattr(mgr, '_base_dir', '') if mgr else ''
            if base:
                return os.path.join(base, 'data', 'group_remarks.json')
        except Exception:
            pass
        return ''

    def _read_remarks_raw(self) -> dict[str, Any]:
        path = self._remarks_path()
        if not path or not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            log.exception('读取 group_remarks.json 失败')
            return {}

    def get_group_remark(self, gid: str) -> dict[str, str]:
        gid = str(gid or '').strip()
        val = self._read_remarks_raw().get(gid, {})
        name, qq = '', ''
        if isinstance(val, dict):
            name = str(val.get('name') or '').strip()
            qq = str(val.get('qq') or '').strip()
        elif isinstance(val, str):
            name = val.strip()
        blob = self._groups().get(gid, {})
        if isinstance(blob, dict):
            if not name:
                name = str(blob.get('name') or '').strip()
            if not qq:
                qq = str(blob.get('group_qq') or '').strip()
        return {'group_id': gid, 'name': name, 'qq': qq}

    def set_group_remark(self, gid: str, name: str = '', qq: str = '') -> dict[str, Any]:
        gid = str(gid or '').strip()
        if not gid:
            return {}
        name = str(name or '').strip()
        qq = str(qq or '').strip()
        remarks: dict[str, Any]
        try:
            from web.tools._message.handlers import (
                _invalidate_remark_caches,
                _load_remarks,
                _save_remarks,
            )

            remarks = dict(_load_remarks())
            if name or qq:
                remarks[gid] = {'name': name, 'qq': qq}
            else:
                remarks.pop(gid, None)
            _save_remarks(remarks)
            _invalidate_remark_caches()
        except Exception:
            log.debug('复用 Web 群备注缓存失败，直接写入文件')
            remarks = self._read_remarks_raw()
            if name or qq:
                remarks[gid] = {'name': name, 'qq': qq}
            else:
                remarks.pop(gid, None)
            path = self._remarks_path()
            if path:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(remarks, f, ensure_ascii=False, indent=2)

        blob = self.touch_group(gid, name)
        if qq:
            blob['group_qq'] = qq
            av = self._qq_group_avatar(qq) or self._qq_group_avatar_alt(qq)
            if av:
                blob['avatar'] = av
        else:
            blob.pop('group_qq', None)
        self.save()

        avatar = str(blob.get('avatar') or '')
        if qq and not avatar:
            avatar = self._qq_group_avatar(qq) or self._qq_group_avatar_alt(qq)
        return {'group_id': gid, 'name': name, 'qq': qq, 'avatar': avatar}

    def series(self, period: str = 'day') -> dict[str, Any]:
        """按 实时/天/月 聚合点歌统计序列，用于曲线图。

        period: 'realtime' / 'day' / 'month'
        返回 { period, labels[], searches[], plays[], users[] }，
        users 为每个时间桶内活跃去重用户数；若该桶无记录则为 0。
        """
        now = int(time.time())
        period = (period or 'day').lower()
        if period == 'realtime':
            # 最近 24 小时，按 1 小时 1 桶
            bucket_secs = 3600
            count = 24
            fmt = '%H:00'
        elif period == 'month':
            bucket_secs = 86400 * 30
            count = 12
            fmt = '%m/%d'
        else:  # day
            bucket_secs = 86400
            count = 14
            fmt = '%m/%d'

        # 构造时间桶列表（从旧到新）
        import datetime
        buckets = []
        end_ts = now
        for i in range(count):
            b_start = end_ts - bucket_secs
            buckets.append({
                'start': b_start,
                'end': end_ts,
                'label': datetime.datetime.fromtimestamp(b_start).strftime(fmt),
            })
            end_ts = b_start
        buckets.reverse()

        searches = [0] * count
        plays = [0] * count
        user_sets = [set() for _ in range(count)]

        for rec in self._records():
            ts = int(rec.get('ts') or 0)
            if not ts:
                continue
            for i, b in enumerate(buckets):
                if b['start'] <= ts < b['end']:
                    rtype = rec.get('type')
                    if rtype == 'play':
                        plays[i] += 1
                    else:
                        searches[i] += 1
                    uid = str(rec.get('uid') or '')
                    if uid:
                        user_sets[i].add(uid)
                    break

        return {
            'period': period,
            'labels': [b['label'] for b in buckets],
            'searches': searches,
            'plays': plays,
            'users': [len(s) for s in user_sets],
        }

    def dashboard(self) -> dict[str, Any]:
        users = self._users()
        user_list = []
        for key, blob in users.items():
            if not isinstance(blob, dict):
                continue
            appid, uid = (key.split(':', 1) + [''])[:2]
            override = blob.get('show_lyrics')
            effective = self.should_show_lyrics(appid, uid, '')
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
        group_meta = self._group_meta_map()
        group_list = []
        for gid, blob in groups.items():
            if not isinstance(blob, dict) or not gid:
                continue
            override = blob.get('show_lyrics')
            effective = self.should_show_lyrics('', '', gid)
            extra = group_meta.get(gid, {})
            member_count = self._resolve_group_member_count(blob, extra)
            avatar = str(blob.get('avatar') or extra.get('avatar') or '')
            group_qq = str(blob.get('group_qq') or extra.get('group_qq') or '')
            if not avatar and group_qq:
                avatar = self._qq_group_avatar(group_qq) or self._qq_group_avatar_alt(group_qq)
            name = self._resolve_group_name(blob, extra)
            group_list.append({
                'group_id': gid,
                'name': name,
                'group_qq': group_qq,
                'avatar': avatar,
                'member_count': member_count,
                'show_lyrics': override,
                'effective_show_lyrics': bool(effective),
                'last_seen': blob.get('last_seen'),
                'searches': int(blob.get('searches', 0) or 0),
                'plays': int(blob.get('plays', 0) or 0),
                'music_source': str(blob.get('music_source') or ''),
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
                'cover': blob.get('cover') or '',
                'url': blob.get('url') or '',
            })
        top.sort(key=lambda x: x['count'], reverse=True)

        pub_global = {
            'show_lyrics': bool(self._global().get('show_lyrics', True)),
            'music_source': str(self._global().get('music_source') or 'aa_qq'),
            'custom_source_url': str(self._global().get('custom_source_url') or ''),
        }
        return {
            'global': pub_global,
            'stats': dict(self._stats()),
            'users': user_list,
            'groups': group_list,
            'top_songs': top[:_TOP_SONGS],
            'recent_records': self.records_view('', 10),
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
