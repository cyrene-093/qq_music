"""QQ 音乐点歌插件：搜索、真实音源、歌词显示开关（个人/群/全局）。"""

from __future__ import annotations

import logging

from core.message.event import INTERACTION_CREATE
from core.plugin.decorators import handler, on_load, on_unload

from plugins.qq_music.settings import get_store
from plugins.qq_music.service import (
    PLAY_ERR_API,
    PLAY_ERR_BAD_INDEX,
    PLAY_ERR_EXPIRED,
    aclose,
    build_lyric_markdown,
    fetch_play_with_lyric,
    format_help_markdown,
    format_search_list,
    get_last_lyric,
    help_buttons,
    play_buttons,
    search_buttons,
    search_songs,
    store_last_lyric,
    store_search,
)

# Web 扩展面板（注册路由与侧栏页）
import plugins.qq_music.web_panel as _qq_music_web  # noqa: F401

__plugin_meta__ = {
    'name': 'QQ音乐点歌',
    'description': 'QQ音乐点歌：搜索/音源播放/完整歌词，歌词显示开关（个人/群/全局），含统计与Web管理面板',
    'version': '1.4.1',
    'license': 'MIT',
}

log = logging.getLogger('plugins.qq_music')

_P = r'(?:#|＃)?'
_STRIP_TBL = str.maketrans('', '', '"\'<>&*_~`[](){}\\\\/:')


def _session_uid(event) -> str:
    """会话缓存键：固定用 member_openid，避免 union 交换后与按钮回调不一致。"""
    raw = str(getattr(event, 'raw_user_id', '') or '').strip()
    if raw:
        return raw
    return str(getattr(event, 'user_id', '') or '')


def _mention_uid(event) -> str:
    return str(getattr(event, 'user_id', '') or '') or _session_uid(event)


def _appid(event) -> str:
    return str(getattr(event, 'appid', '') or '')


def _gid(event) -> str:
    return str(getattr(event, 'group_id', '') or '')


def _nickname(event) -> str:
    return str(getattr(event, 'username', '') or '').strip()


def _touch_user(event) -> None:
    get_store().touch_user(_appid(event), _session_uid(event), _nickname(event))


_GROUP_LYRIC_OFF_MSG = (
    '本群管理员/群主已关闭本群歌词显示，无法开启；如需恢复请联系群主/管理员，'
    '或由管理员发送 `#群歌词开`。'
)


def _is_group_admin(event) -> bool:
    return getattr(event, 'member_role', '') in ('admin', 'owner')


def _group_lyrics_blocked(event, store) -> bool:
    """群主/管理员已显式关闭本群歌词，且操作者不是管理员（按钮回调无法取角色，按普通成员处理）。"""
    gid = _gid(event)
    if not gid or not store.group_lyrics_forced_off(gid):
        return False
    return not _is_group_admin(event)


def _music_filename(data: dict, music_url: str) -> str:
    song = (str(data.get('song') or '未知').translate(_STRIP_TBL).strip()[:36]) or '未知'
    singer = (str(data.get('singer') or '').translate(_STRIP_TBL).strip()[:18])
    url = music_url.lower()
    if '.mp3' in url:
        ext = '.mp3'
    elif '.wav' in url:
        ext = '.wav'
    elif '.ogg' in url:
        ext = '.ogg'
    else:
        ext = '.m4a'
    name = song if not singer else f'{song} - {singer}'
    return f'{name}{ext}'


def _play_info_markdown(data: dict, music_url: str = '', *, lyrics_hint: str = '') -> str:
    song = (str(data.get('song') or '未知').translate(_STRIP_TBL).strip()[:50]) or '未知'
    singer = (str(data.get('singer') or '未知').translate(_STRIP_TBL).strip()[:50]) or '未知'
    album = (str(data.get('album_name') or '').translate(_STRIP_TBL).strip()[:50])
    lines = [
        f'## 🎵 {song}',
        '──────────────',
        f'**歌手**：{singer}',
        f'**专辑**：{album or "未知"}',
    ]
    if lyrics_hint:
        lines.extend(['', lyrics_hint])
    if music_url:
        lines.extend(['', f'[备用播放链接]({music_url})'])
    return '\n'.join(lines)


async def _reply_md(
    event,
    content: str,
    buttons=None,
    mention: bool = True,
    *,
    force_verify_image: bool = False,
) -> bool | None:
    uid = _mention_uid(event)
    head = f'<@{uid}>\n' if mention and uid else ''
    text = f'{head}{content}' if head else content
    return await event.reply(
        text,
        buttons=buttons,
        msg_type=2,
        skip_suffix=True,
        force_verify_image_resource=force_verify_image,
    )


async def _send_lyric(event) -> None:
    _touch_user(event)
    result = get_last_lyric(_session_uid(event), _appid(event), _gid(event))
    if not result:
        await _reply_md(
            event,
            '暂无歌词缓存，请先 `#点歌 歌名` 再 `#听1`（搜索列表 3 分钟内有效）。',
            buttons=help_buttons(),
        )
        return
    await _reply_md(event, result['markdown'], buttons=play_buttons())


async def _send_music_audio(event, music_url: str, data: dict, caption: str) -> None:
    """优先语音条；失败（常见为时长超限）则改发文件。"""
    file_name = _music_filename(data, music_url)
    sent = await event.reply_voice(music_url, caption)
    if sent:
        return
    err = getattr(event, 'error', None)
    log.info('语音发送失败，改用文件: url=%s err=%s', music_url, err)
    sent = await event.reply_file(music_url, caption, file_name=file_name)
    if sent:
        return
    log.warning('歌曲文件发送失败 url=%s err=%s', music_url, getattr(event, 'error', None))
    await _reply_md(
        event,
        f'音频发送失败，请直接打开播放链接：\n{music_url}',
        buttons=play_buttons(),
    )


async def _play_song(event, index: int) -> None:
    _touch_user(event)
    uid, appid, gid = _session_uid(event), _appid(event), _gid(event)
    store = get_store()
    try:
        data, lyric_bundle, err = await fetch_play_with_lyric(uid, appid, gid, index)
    except Exception:
        log.exception('获取音源失败')
        await _reply_md(event, '网络请求超时，请稍后重试。', buttons=play_buttons())
        return
    if not data:
        if err == PLAY_ERR_EXPIRED:
            msg = '搜索列表已过期（仅保留 3 分钟），请重新 `#点歌 歌名` 再 `#听1`。'
        elif err == PLAY_ERR_BAD_INDEX:
            msg = '序号无效，请使用列表中的 1–10，或重新 `#点歌 歌名`。'
        elif err == PLAY_ERR_API:
            msg = '获取歌曲失败，请稍后重试或重新 `#点歌` 搜索。'
        else:
            msg = '无法播放，请重新 `#点歌 歌名` 后再试。'
        await _reply_md(event, msg, buttons=help_buttons())
        return
    music_url = str(data.get('music') or '').strip()
    if not music_url:
        await _reply_md(event, '未获取到歌曲链接，请换一首试试。', buttons=play_buttons())
        return

    store.record_play(
        song=str(data.get('song') or ''),
        singer=str(data.get('singer') or ''),
        appid=appid,
        uid=uid,
        gid=gid,
        nickname=_nickname(event),
    )
    show_lyrics = store.should_show_lyrics(appid, uid, gid)
    lyrics_hint = ''
    if lyric_bundle.lines and not show_lyrics:
        if gid and store.group_lyrics_forced_off(gid):
            lyrics_hint = '本群歌词已被管理员/群主关闭，无法自行开启（可联系管理员）。'
        else:
            lyrics_hint = '歌词已关闭，发送 `#歌词开` 开启，或 `#歌词` 手动查看。'

    await _reply_md(
        event,
        _play_info_markdown(data, music_url, lyrics_hint=lyrics_hint),
        buttons=play_buttons(),
    )

    if lyric_bundle.lines:
        song = str(data.get('song') or '未知')
        lyric_md = build_lyric_markdown(lyric_bundle, song)
        store_last_lyric(uid, appid, gid, song, lyric_md)
        if show_lyrics:
            store.record_lyric_sent()
            await _reply_md(event, lyric_md, buttons=play_buttons())
        else:
            store.record_lyric_skipped()

    mention = _mention_uid(event)
    song = data.get('song') or '未知'
    singer = data.get('singer') or ''
    caption = f'<@{mention}>\n{song}'
    if singer:
        caption += f' · {singer}'
    caption += '\nQQ音乐'
    await _send_music_audio(event, music_url, data, caption)


@on_load
def _boot():
    get_store()


@on_unload
async def _shutdown():
    await aclose()


@handler(rf'^{_P}音乐帮助$', name='#音乐帮助', desc='查看 QQ 音乐点歌用法', priority=28, block=True)
async def cmd_music_help(event, match):
    _touch_user(event)
    await _reply_md(event, format_help_markdown(), buttons=help_buttons())


@handler(rf'^{_P}点歌(?:\s+(.+))?$', name='#点歌', desc='搜索或播放：#点歌 歌名 / #点歌 1', priority=25, block=True)
async def cmd_song(event, match):
    _touch_user(event)
    arg = (match.group(1) or '').strip()
    if not arg:
        await _reply_md(event, format_help_markdown(), buttons=help_buttons())
        return
    uid, appid, gid = _session_uid(event), _appid(event), _gid(event)
    if arg.isdigit():
        await _play_song(event, int(arg))
        return
    try:
        songs = await search_songs(arg)
        if not songs:
            await _reply_md(event, f'未找到「{arg}」相关歌曲。', buttons=help_buttons())
            return
        store_search(uid, appid, gid, arg, songs)
        get_store().record_search(
            keyword=arg,
            appid=appid,
            uid=uid,
            gid=gid,
            nickname=_nickname(event),
        )
        md = format_search_list(songs, arg, group_chat=bool(gid))
        ok = await _reply_md(event, md, buttons=search_buttons(), force_verify_image=True)
        if ok is False:
            await _reply_md(event, md, buttons=search_buttons(), force_verify_image=False)
    except Exception:
        log.exception('点歌搜索失败')
        await _reply_md(event, '网络请求超时，请稍后重试。', buttons=help_buttons())


@handler(rf'^{_P}听(\d+)$', name='#听N', desc='播放搜索结果第 N 首，如 #听1', priority=24, block=True)
async def cmd_listen(event, match):
    await _play_song(event, int(match.group(1)))


@handler(rf'^{_P}歌词$', name='#歌词', desc='查看完整歌词', priority=24, block=True)
async def cmd_lyric(event, match):
    await _send_lyric(event)


@handler(rf'^{_P}歌词开关$', name='#歌词开关', desc='切换个人歌词显示开关', priority=24, block=True)
async def cmd_lyric_toggle(event, match):
    _touch_user(event)
    store = get_store()
    appid, uid, gid = _appid(event), _session_uid(event), _gid(event)
    if store.group_lyrics_forced_off(gid):
        if _is_group_admin(event):
            await _reply_md(
                event,
                '本群歌词已被关闭。如需开启请发送 `#群歌词开`（或使用 Web 面板群歌词控制）。',
                buttons=help_buttons(),
            )
            return
        await _reply_md(event, _GROUP_LYRIC_OFF_MSG, buttons=help_buttons())
        return
    new_val = store.toggle_user_show_lyrics(appid, uid, _nickname(event))
    state = '开启' if new_val else '关闭'
    await _reply_md(
        event,
        f'歌词显示已**{state}**。\n播放时将{"自动发送歌词" if new_val else "不再自动发送歌词"}（仍可用 `#歌词` 手动查看）。',
        buttons=help_buttons(),
    )


@handler(rf'^{_P}歌词开$', name='#歌词开', desc='开启个人歌词显示', priority=24, block=True)
async def cmd_lyric_on(event, match):
    _touch_user(event)
    store = get_store()
    if _group_lyrics_blocked(event, store):
        await _reply_md(event, _GROUP_LYRIC_OFF_MSG, buttons=help_buttons())
        return
    get_store().set_user_show_lyrics(_appid(event), _session_uid(event), True, _nickname(event))
    await _reply_md(event, '歌词显示已**开启**，播放时将自动发送歌词。', buttons=help_buttons())


@handler(rf'^{_P}歌词关$', name='#歌词关', desc='关闭个人歌词显示', priority=24, block=True)
async def cmd_lyric_off(event, match):
    _touch_user(event)
    get_store().set_user_show_lyrics(_appid(event), _session_uid(event), False, _nickname(event))
    await _reply_md(
        event,
        '歌词显示已**关闭**，播放时不再自动发送歌词（可用 `#歌词` 手动查看）。',
        buttons=help_buttons(),
    )


async def _set_group_lyrics(event, value: bool) -> None:
    """群歌词开关（仅群主/管理员），优先级：个人 > 群 > 全局。"""
    _touch_user(event)
    gid = _gid(event)
    if not gid:
        await _reply_md(
            event,
            '群歌词开关仅限群聊使用；私聊请用 `#歌词开` / `#歌词关` 设置个人偏好。',
            buttons=help_buttons(),
        )
        return
    if getattr(event, 'member_role', '') not in ('admin', 'owner'):
        await _reply_md(event, '仅**群主/管理员**可控制本群歌词显示。', buttons=help_buttons())
        return
    get_store().set_group_show_lyrics(gid, value)
    state = '开启' if value else '关闭'
    await _reply_md(
        event,
        (
            f'本群歌词显示已**{state}**。\n'
            + (
                '播放时将自动发送歌词，成员仍可自行用 `#歌词开/关` 设置个人偏好。'
                if value
                else '播放时不再自动发送歌词；**普通成员无法自行开启**，'
                     '如需恢复请管理员/群主发送 `#群歌词开` 或在 Web 面板开启本群歌词。'
            )
        ),
        buttons=help_buttons(),
    )


@handler(rf'^{_P}群歌词开$', name='#群歌词开', desc='开启本群歌词显示(群主/管理)', priority=24, block=True)
async def cmd_group_lyric_on(event, match):
    await _set_group_lyrics(event, True)


@handler(rf'^{_P}群歌词关$', name='#群歌词关', desc='关闭本群歌词显示(群主/管理)', priority=24, block=True)
async def cmd_group_lyric_off(event, match):
    await _set_group_lyrics(event, False)


@handler(rf'^{_P}群歌词状态$', name='#群歌词状态', desc='查看本群歌词设置', priority=24, block=True)
async def cmd_group_lyric_status(event, match):
    _touch_user(event)
    gid = _gid(event)
    if not gid:
        await _reply_md(event, '本指令仅限群聊使用。', buttons=help_buttons())
        return
    store = get_store()
    gval = store.get_group_show_lyrics(gid)
    g_state = '跟随全局' if gval is None else ('开启' if gval else '关闭')
    g_name = (store.touch_group(gid).get('name') or '').strip()
    head = f'**{g_name}**\n' if g_name else ''
    await _reply_md(
        event,
        f'{head}本群歌词显示：**{g_state}**\n'
        f'全局默认：**{"开启" if store.get_global_show_lyrics() else "关闭"}**\n'
        '群主/管理员可用 `#群歌词开` / `#群歌词关` 单独控制本群。',
        buttons=help_buttons(),
    )


def _resolve_appid(event, store) -> str:
    """按钮回调事件通常不携带 appid：优先取事件值，否则从已有用户记录反查。"""
    appid = _appid(event)
    if appid:
        return appid
    uid = _session_uid(event)
    if uid:
        for key in store._users():
            if key.endswith(':' + uid):
                return key.split(':', 1)[0]
    return ''


@handler(
    r'^qm:lyric:toggle$',
    name='歌词开关按钮',
    desc='处理歌词开关按钮回调（开/关一键切换，与 #歌词开关 同一持久化偏好）',
    event_types=[INTERACTION_CREATE],
    priority=90,
    block=True,
)
async def lyric_toggle_button(event, match):
    store = get_store()
    gid = str(getattr(event, 'group_id', '') or '').strip() or str(getattr(event, 'group_openid', '') or '').strip()
    if gid and store.group_lyrics_forced_off(gid):
        # 按钮回调拿不到 member_role，统一按普通成员处理：群已关闭时禁止开启
        await _reply_md(event, _GROUP_LYRIC_OFF_MSG, buttons=help_buttons())
        return
    new_val = store.toggle_user_show_lyrics(
        _resolve_appid(event, store), _session_uid(event), _nickname(event)
    )
    state = '开启' if new_val else '关闭'
    await _reply_md(
        event,
        f'歌词显示已**{state}**。\n播放时将{"自动发送歌词" if new_val else "不再自动发送歌词"}（可用 `#歌词` 手动查看）。',
        buttons=help_buttons(),
    )
