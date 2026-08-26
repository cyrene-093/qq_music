"""QQ 音乐点歌插件：搜索、真实音源、歌词显示开关（个人/群/全局）。"""

from __future__ import annotations

import os

from core.base.logger import PLUGIN, get_logger
from core.message.event import INTERACTION_CREATE
from core.plugin.decorators import handler, on_load, on_unload
from core.plugin.web_pages import register_page, unregister_page

from plugins.qq_music.settings import get_store
from plugins.qq_music.service import (
    PLAY_ERR_API,
    PLAY_ERR_BAD_INDEX,
    PLAY_ERR_EXPIRED,
    aclose,
    fetch_play_with_lyric,
    format_group_song_help,
    format_help_markdown,
    format_other_user_prompt,
    format_play_detail_markdown,
    format_search_list,
    group_play_buttons,
    group_search_buttons,
    has_active_search,
    help_buttons,
    other_user_buttons,
    parse_list_index,
    peek_peer_search,
    peer_prompt_keyword,
    play_buttons,
    search_buttons,
    search_songs,
    store_group_search,
    store_search,
    _song_cover_url,
)
from plugins.qq_music.sources import (
    format_source_help,
    get_source,
    resolve_source,
    source_switch_buttons,
)

# 必须导入：注册 /api/ext/qq_music/* 路由（装饰器在 import 时生效）
try:
    from . import web_panel as _web_panel  # noqa: F401
except Exception:
    from importlib import import_module

    import_module('plugins.qq_music.web_panel')

_PAGE_KEY = 'qq-music-panel-v2'
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PANEL_HTML = os.path.join(_PLUGIN_DIR, 'panel.html')

__plugin_meta__ = {
    'name': 'QQ音乐点歌',
    'author': '飞行漂绒',
    'description': 'QQ 点歌：个人/群点歌、多歌源、LRC、Web 面板。第三方音源，请遵守版权，建议自用娱乐。',
    'version': '1.9.5',
    'license': 'MIT',
    # 上架市场前改成你的真实仓库地址
    'github': '',
}

log = get_logger(PLUGIN, 'qq_music')


def _ensure_web_page(*, quiet: bool = False) -> bool:
    """注册侧边栏扩展页。import 时与 on_load 各调一次，避免热重载竞态丢注册。"""
    if os.path.isfile(_PANEL_HTML):
        register_page(
            key=_PAGE_KEY,
            label='QQ音乐',
            source='plugin',
            source_name='qq_music',
            icon='music',
            html_file=_PANEL_HTML,
        )
        if not quiet:
            log.info('QQ音乐 Web 面板已注册 key=%s file=%s', _PAGE_KEY, _PANEL_HTML)
        return True
    # 缺 panel.html 时仍注册占位页，避免 iframe 只显示「页面不存在」JSON
    tip = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>QQ音乐</title></head>'
        '<body style="font-family:sans-serif;padding:24px;line-height:1.6">'
        '<h2>panel.html 缺失</h2>'
        '<p>请完整安装 <code>qq_music</code> 插件目录（需包含 <code>panel.html</code>），'
        '然后在「插件模块」中重载本插件，并强制刷新浏览器（Ctrl+F5）。</p>'
        f'<p style="color:#888">查找路径：{_PANEL_HTML}</p>'
        '</body></html>'
    )
    register_page(
        key=_PAGE_KEY,
        label='QQ音乐',
        source='plugin',
        source_name='qq_music',
        icon='music',
        html=tip,
    )
    log.error('QQ音乐 panel.html 不存在: %s', _PANEL_HTML)
    return False


# 入口加载时立即注册，不单独依赖 on_load（热重载竞态下可缩短「页面不存在」窗口）
_ensure_web_page(quiet=True)

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


async def _send_music_audio(event, music_url: str, data: dict, caption: str, buttons=None) -> None:
    """优先语音条；失败（常见为时长超限）则改发文件。"""
    btns = buttons if buttons is not None else play_buttons()
    if not music_url or not music_url.startswith(('http://', 'https://')):
        await _reply_md(
            event,
            '音频链接无效，请重新点歌或换一首试试。',
            buttons=btns,
        )
        return

    file_name = _music_filename(data, music_url)
    
    # 尝试发送语音
    try:
        sent = await event.reply_voice(music_url, caption)
        if sent:
            log.info('语音发送成功: %s', music_url)
            return
        err = getattr(event, 'error', None)
        log.info('语音发送返回失败，改用文件: url=%s err=%s', music_url, err)
    except Exception as e:
        log.warning('语音发送异常，改用文件: url=%s err=%s', music_url, e)
    
    # 语音失败，尝试发送文件
    try:
        sent = await event.reply_file(music_url, caption, file_name=file_name)
        if sent:
            log.info('文件发送成功: %s', music_url)
            return
        err = getattr(event, 'error', None)
        log.warning('文件发送返回失败: url=%s err=%s', music_url, err)
    except Exception as e:
        log.warning('文件发送异常: url=%s err=%s', music_url, e)
    
    # 全部失败，提示用户
    await _reply_md(
        event,
        f'⚠️ 音频发送失败，可能原因：\n'
        f'• 歌曲时长超限（QQ限制60秒内）\n'
        f'• 音频格式不支持\n'
        f'• 网络传输异常\n\n'
        f'请直接打开备用播放链接：\n{music_url}',
        buttons=btns,
    )


async def _play_song(event, index: int, *, shared: bool = False) -> None:
    _touch_user(event)
    uid, appid, gid = _session_uid(event), _appid(event), _gid(event)
    store = get_store()
    btns = group_play_buttons() if shared else play_buttons()
    search_cmd = '#群点歌 歌名' if shared else '#点歌 歌名'
    listen_cmd = '#群听1' if shared else '#听1'
    try:
        data, lyric_bundle, err = await fetch_play_with_lyric(
            uid, appid, gid, index, shared=shared,
        )
    except Exception:
        log.exception('获取音源失败')
        await _reply_md(event, '网络请求超时，请稍后重试。', buttons=btns)
        return
    if not data:
        if err == PLAY_ERR_EXPIRED:
            if not shared:
                peer = peek_peer_search(uid, appid, gid)
                if peer:
                    kw = peer_prompt_keyword(peer, index)
                    await _reply_md(
                        event,
                        format_other_user_prompt(kw, group_chat=bool(gid)),
                        buttons=other_user_buttons(kw),
                    )
                    return
                if has_active_search(uid, appid, gid, shared=True):
                    await _reply_md(
                        event,
                        f'`#听{index}` 是**个人**点歌。本群有共享列表，请发送 `#群听{index}`；'
                        '或先 `#点歌 歌名` 生成你自己的列表。',
                        buttons=group_search_buttons(),
                    )
                    return
            msg = f'搜索列表已过期（仅保留 3 分钟），请重新 `{search_cmd}` 再 `{listen_cmd}`。'
        elif err == PLAY_ERR_BAD_INDEX:
            msg = f'序号无效，请使用列表中的 1–10，或重新 `{search_cmd}`。'
        elif err == PLAY_ERR_API:
            msg = (
                f'获取歌曲失败。可发送 `#歌源` 切换其他音源后重新 `{search_cmd}`。'
            )
        else:
            msg = f'无法播放，请重新 `{search_cmd}` 后再试。'
        await _reply_md(event, msg, buttons=help_buttons())
        return
    music_url = str(data.get('music') or '').strip()
    if not music_url:
        await _reply_md(event, '未获取到歌曲链接，请换一首试试。', buttons=btns)
        return

    store.record_play(
        song=str(data.get('song') or ''),
        singer=str(data.get('singer') or ''),
        keyword=str(data.get('_keyword') or ''),
        appid=appid,
        uid=uid,
        gid=gid,
        nickname=_nickname(event),
        cover=_song_cover_url(data),
        url=music_url,
        scope='group' if shared else 'personal',
    )
    show_lyrics = store.should_show_lyrics(appid, uid, gid)
    lyrics_hint = ''
    if lyric_bundle.lines and not show_lyrics:
        if gid and store.group_lyrics_forced_off(gid):
            lyrics_hint = '本群歌词已被管理员/群主关闭，无法自行开启（可联系管理员）。'
        else:
            lyrics_hint = '歌词已关闭，发送 `#歌词开` 可在播放时显示歌词。'

    if lyric_bundle.lines and show_lyrics:
        store.record_lyric_sent()
    elif lyric_bundle.lines:
        store.record_lyric_skipped()

    detail_md = format_play_detail_markdown(
        data,
        lyric_bundle,
        show_lyrics=show_lyrics,
        lyrics_hint=lyrics_hint,
        music_url=music_url,
    )
    has_cover = '![' in detail_md and '](http' in detail_md
    
    # 发送播放详情（含歌词）
    try:
        ok = await _reply_md(
            event,
            detail_md,
            buttons=btns,
            force_verify_image=has_cover,
        )
        if ok is False and has_cover:
            await _reply_md(event, detail_md, buttons=btns, force_verify_image=False)
    except Exception as e:
        log.warning('播放详情发送失败: %s', e)
    
    # 发送语音/文件（确保一定会执行）
    mention = _mention_uid(event)
    song = data.get('song') or '未知'
    singer = data.get('singer') or ''
    caption = f'<@{mention}>\n{song}'
    if singer:
        caption += f' · {singer}'
    caption += '\nQQ音乐'
    
    try:
        await _send_music_audio(event, music_url, data, caption, buttons=btns)
    except Exception as e:
        log.exception('音频发送异常')
        await _reply_md(
            event,
            f'⚠️ 音频发送失败：{e}\n\n请直接打开备用播放链接：\n{music_url}',
            buttons=btns,
        )


@on_load
async def _boot():
    # 先挂页面，再初始化存储，避免 store 异常导致侧边栏页丢失
    _ensure_web_page()
    try:
        get_store()
    except Exception:
        log.exception('初始化点歌设置失败')


@on_unload
async def _shutdown():
    try:
        await aclose()
    except Exception:
        log.debug('关闭 http 客户端失败', exc_info=True)
    unregister_page(_PAGE_KEY)
    log.info('QQ音乐 Web 面板已注销 key=%s', _PAGE_KEY)


@handler(rf'^{_P}音乐帮助$', name='#音乐帮助', desc='查看 QQ 音乐点歌用法', priority=28, block=True)
async def cmd_music_help(event, match):
    _touch_user(event)
    await _reply_md(event, format_help_markdown(), buttons=help_buttons())


def _can_switch_source(event) -> bool:
    if _is_group_admin(event):
        return True
    return _is_bot_owner(event, _appid(event))


@handler(rf'^{_P}(?:切换)?歌源\s*(.*)$', name='#歌源', desc='查看或切换点歌音源', priority=27, block=True)
async def cmd_music_source(event, match):
    _touch_user(event)
    store = get_store()
    gid = _gid(event)
    custom = store.get_custom_source_url()
    current = get_source(store.get_music_source(gid), custom)
    arg = (match.group(1) or '').strip()
    if not arg:
        await _reply_md(
            event,
            format_source_help(current, group=bool(gid), custom_url=custom),
            buttons=source_switch_buttons(custom),
        )
        return
    if not _can_switch_source(event):
        await _reply_md(
            event,
            '仅**群主/管理员/机器人主人**可切换歌源。当前：**' + current.name + '**',
            buttons=source_switch_buttons(custom),
        )
        return
    follow = arg.lower() in ('跟随', '跟随全局', 'global', 'follow', '默认全局')
    if follow:
        if not gid:
            await _reply_md(event, '私聊没有群覆盖，请直接 `#歌源 1` 切换全局歌源。', buttons=source_switch_buttons(custom))
            return
        new_id = store.set_music_source('follow', gid)
        src = get_source(new_id, custom)
        await _reply_md(event, f'本群歌源已**跟随全局**：{src.name}', buttons=source_switch_buttons(custom))
        return
    src = resolve_source(arg, custom)
    if not src:
        await _reply_md(
            event,
            '未识别的歌源。请发送 `#歌源` 查看可用列表。',
            buttons=source_switch_buttons(custom),
        )
        return
    new_id = store.set_music_source(src.id, gid)
    src = get_source(new_id, custom)
    scope = '本群' if gid else '全局'
    note = f'\n{src.note}' if src.note else ''
    await _reply_md(event, f'{scope}歌源已切换为 **{src.name}**。{note}\n请重新 `#点歌` 生效。', buttons=source_switch_buttons(custom))


async def _search_and_reply(event, keyword: str, *, shared: bool) -> None:
    uid, appid, gid = _session_uid(event), _appid(event), _gid(event)
    btns = group_search_buttons() if shared else search_buttons()
    store = get_store()
    src = get_source(store.get_music_source(gid), store.get_custom_source_url())
    try:
        songs = await search_songs(keyword, group_id=gid)
        if not songs:
            await _reply_md(event, f'未找到「{keyword}」相关歌曲。', buttons=help_buttons())
            return
        if shared:
            store_group_search(
                appid, gid, keyword, songs,
                uid=uid, nickname=_nickname(event),
            )
        else:
            store_search(uid, appid, gid, keyword, songs)
        store.record_search(
            keyword=keyword,
            appid=appid,
            uid=uid,
            gid=gid,
            nickname=_nickname(event),
            scope='group' if shared else 'personal',
        )
        md = format_search_list(
            songs, keyword, group_chat=bool(gid), shared=shared, source_name=src.name,
        )
        ok = await _reply_md(event, md, buttons=btns, force_verify_image=True)
        if ok is False:
            await _reply_md(event, md, buttons=btns, force_verify_image=False)
    except Exception:
        log.exception('点歌搜索失败')
        await _reply_md(event, '网络请求超时，请稍后重试。', buttons=help_buttons())


@handler(rf'^{_P}群点歌\s*(.*)$', name='#群点歌', desc='本群共享点歌：#群点歌 歌名', priority=26, block=True)
async def cmd_group_song(event, match):
    _touch_user(event)
    gid = _gid(event)
    if not gid:
        await _reply_md(
            event,
            '群点歌仅限群聊使用；私聊请用 `#点歌 歌名`。',
            buttons=help_buttons(),
        )
        return
    arg = (match.group(1) or '').strip()
    if not arg:
        await _reply_md(event, format_group_song_help(), buttons=group_search_buttons())
        return
    idx = parse_list_index(arg)
    if idx and has_active_search(_session_uid(event), _appid(event), gid, shared=True):
        await _play_song(event, idx, shared=True)
        return
    await _search_and_reply(event, arg, shared=True)


@handler(rf'^{_P}(?!群)点歌\s*(.*)$', name='#点歌', desc='个人搜索歌曲：#点歌 歌名', priority=25, block=True)
async def cmd_song(event, match):
    _touch_user(event)
    arg = (match.group(1) or '').strip()
    if not arg:
        await _reply_md(event, format_help_markdown(), buttons=help_buttons())
        return
    uid, appid, gid = _session_uid(event), _appid(event), _gid(event)
    idx = parse_list_index(arg)
    if idx and has_active_search(uid, appid, gid, shared=False):
        await _play_song(event, idx, shared=False)
        return
    await _search_and_reply(event, arg, shared=False)


@handler(rf'^{_P}群听(\d+)$', name='#群听N', desc='播放群点歌列表第 N 首，如 #群听1', priority=25, block=True)
async def cmd_group_listen(event, match):
    if not _gid(event):
        await _reply_md(
            event,
            '群听仅限群聊使用；私聊请用 `#点歌` / `#听1`。',
            buttons=help_buttons(),
        )
        return
    await _play_song(event, int(match.group(1)), shared=True)


@handler(rf'^{_P}(?!群)听(\d+)$', name='#听N', desc='播放个人搜索结果第 N 首，如 #听1', priority=24, block=True)
async def cmd_listen(event, match):
    await _play_song(event, int(match.group(1)), shared=False)


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
        f'歌词显示已**{state}**。\n播放时将{"附带 LRC 歌词" if new_val else "不再附带歌词"}。',
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
    await _reply_md(event, '歌词显示已**开启**，播放时将附带 LRC 歌词。', buttons=help_buttons())


@handler(rf'^{_P}歌词关$', name='#歌词关', desc='关闭个人歌词显示', priority=24, block=True)
async def cmd_lyric_off(event, match):
    _touch_user(event)
    get_store().set_user_show_lyrics(_appid(event), _session_uid(event), False, _nickname(event))
    await _reply_md(
        event,
        '歌词显示已**关闭**，播放时不再附带歌词（发送 `#歌词开` 可恢复）。',
        buttons=help_buttons(),
    )


async def _set_group_lyrics(event, value: bool) -> None:
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
                '播放时将附带歌词，成员仍可自行用 `#歌词开/关` 设置个人偏好。'
                if value
                else '播放时不再附带歌词；**普通成员无法自行开启**，'
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


def _is_bot_owner(event, appid: str = '') -> bool:
    """机器人主人：bot 配置 owner_ids 中的成员。"""
    try:
        from core.base.config import cfg as _global_cfg
    except Exception:
        return False
    candidates = {str(_session_uid(event))}
    raw_uid = str(getattr(event, 'user_id', '') or '')
    if raw_uid:
        candidates.add(raw_uid)
    if not candidates or candidates == {''}:
        return False
    try:
        bot_cfg = _global_cfg.get_bot_config(appid) or {}
    except Exception:
        bot_cfg = {}
    owner_ids = {str(x) for x in (bot_cfg.get('owner_ids') or [])}
    return bool(candidates & owner_ids)


@handler(
    r'^qm:group_lyric:toggle$',
    name='群歌词开关按钮',
    desc='群主/管理员/机器人主人 一键开关本群歌词',
    event_types=[INTERACTION_CREATE],
    priority=90,
    block=True,
)
async def _on_group_lyric_button(event, match):
    _touch_user(event)
    store = get_store()
    gid = _gid(event)
    if not gid:
        await _reply_md(
            event,
            '群歌词开关仅限群聊使用；请用 `#歌词开` / `#歌词关` 设置个人偏好。',
            buttons=help_buttons(),
        )
        return
    appid = _resolve_appid(event, store)
    if not (_is_group_admin(event) or _is_bot_owner(event, appid)):
        await _reply_md(
            event,
            '仅**群主/管理员/机器人主人**可控制本群歌词显示。',
            buttons=help_buttons(),
        )
        return
    new_val = store.toggle_group_show_lyrics(gid)
    state = '开启' if new_val else '关闭'
    await _reply_md(
        event,
        (
            f'本群歌词显示已**{state}**。\n'
            + (
                '播放时将附带歌词，成员仍可自行用 `#歌词开/关` 设置个人偏好。'
                if new_val
                else '播放时不再附带歌词；**普通成员无法自行开启**，'
                     '如需恢复请群主/管理员发送 `#群歌词开` 或在 Web 面板开启本群歌词。'
            )
        ),
        buttons=help_buttons(),
    )


def _resolve_appid(event, store) -> str:
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
    desc='处理歌词开关按钮回调',
    event_types=[INTERACTION_CREATE],
    priority=90,
    block=True,
)
async def lyric_toggle_button(event, match):
    store = get_store()
    gid = str(getattr(event, 'group_id', '') or '').strip() or str(getattr(event, 'group_openid', '') or '').strip()
    if gid and store.group_lyrics_forced_off(gid):
        if _is_group_admin(event) or _is_bot_owner(event, _resolve_appid(event, store)):
            await _reply_md(
                event,
                '本群歌词已被关闭。群主/管理员请发送 `#群歌词开` 恢复，或在 Web 面板开启。',
                buttons=help_buttons(),
            )
        else:
            await _reply_md(event, _GROUP_LYRIC_OFF_MSG, buttons=help_buttons())
        return
    new_val = store.toggle_user_show_lyrics(
        _resolve_appid(event, store), _session_uid(event), _nickname(event)
    )
    state = '开启' if new_val else '关闭'
    await _reply_md(
        event,
        f'歌词显示已**{state}**。\n播放时将{"附带 LRC 歌词" if new_val else "不再附带歌词"}。',
        buttons=help_buttons(),
    )
