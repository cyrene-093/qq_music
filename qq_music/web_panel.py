"""QQ 音乐点歌 Web 扩展面板：统计概览 / 点歌记录 / 用户 / 群歌词控制。

HTML 模板位于同目录 panel.html，路由与数据接口保持向后兼容。
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

from core.plugin.decorators import on_load, on_unload
from core.plugin.web_pages import register_page, register_route, unregister_page

from plugins.qq_music.settings import get_store

log = logging.getLogger('plugins.qq_music.web')

_PAGE_KEY = 'qq-music-panel'
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PANEL_HTML = os.path.join(_PLUGIN_DIR, 'panel.html')


async def _json_body(request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@register_route('GET', '/api/ext/qq_music/stats', auth=True)
async def api_stats(request):
    return web.json_response({'ok': True, 'data': get_store().dashboard()})


@register_route('GET', '/api/ext/qq_music/records', auth=True)
async def api_records(request):
    rec_type = (request.query.get('type') or '').strip()
    try:
        limit = int(request.query.get('limit') or 200)
    except (TypeError, ValueError):
        limit = 200
    records = get_store().records_view(rec_type, limit)
    return web.json_response({'ok': True, 'data': {'records': records}})


@register_route('GET', '/api/ext/qq_music/settings', auth=True)
async def api_get_settings(request):
    store = get_store()
    return web.json_response({
        'ok': True,
        'show_lyrics': store.get_global_show_lyrics(),
    })


@register_route('POST', '/api/ext/qq_music/settings', auth=True)
async def api_post_settings(request):
    body = await _json_body(request)
    show = body.get('show_lyrics', True)
    val = get_store().set_global_show_lyrics(bool(show))
    return web.json_response({'ok': True, 'show_lyrics': val})


@register_route('POST', '/api/ext/qq_music/user_settings', auth=True)
async def api_post_user_settings(request):
    body = await _json_body(request)
    user_key = str(body.get('user_key') or '').strip()
    if ':' not in user_key:
        return web.json_response({'ok': False, 'error': 'user_key 格式应为 appid:user_id'}, status=400)
    store = get_store()
    raw = body.get('show_lyrics')
    appid, uid = user_key.split(':', 1)
    nickname = str(body.get('nickname') or '')
    if raw is None:
        effective = store.set_user_show_lyrics(appid, uid, None, nickname)
    else:
        effective = store.set_user_show_lyrics(appid, uid, bool(raw), nickname)
    return web.json_response({'ok': True, 'effective_show_lyrics': effective})


@register_route('POST', '/api/ext/qq_music/group_settings', auth=True)
async def api_post_group_settings(request):
    body = await _json_body(request)
    gid = str(body.get('group_id') or '').strip()
    if not gid:
        return web.json_response({'ok': False, 'error': 'group_id 不能为空'}, status=400)
    store = get_store()
    name = str(body.get('name') or '')
    raw = body.get('show_lyrics')
    if raw is None:
        effective = store.set_group_show_lyrics(gid, None, name)
    else:
        effective = store.set_group_show_lyrics(gid, bool(raw), name)
    return web.json_response({'ok': True, 'effective_show_lyrics': effective})


@on_load
def _register_web_page():
    register_page(
        key=_PAGE_KEY,
        label='QQ音乐',
        source='plugin',
        source_name='qq_music',
        icon='music',
        html_file=_PANEL_HTML,
    )
    log.info('QQ音乐 Web 面板已注册 key=%s html=%s', _PAGE_KEY, _PANEL_HTML)


@on_unload
def _unregister_web_page():
    unregister_page(_PAGE_KEY)
    log.info('QQ音乐 Web 面板已注销 key=%s', _PAGE_KEY)