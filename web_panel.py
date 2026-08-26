"""QQ 音乐点歌 Web 扩展面板：统计概览 / 点歌记录 / 用户 / 群歌词控制。

HTML 模板位于同目录 panel.html，路由与数据接口保持向后兼容。
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

from core.plugin.web_pages import register_route

from .settings import get_store

log = logging.getLogger('plugins.qq_music.web')

_PAGE_KEY = 'qq-music-panel-v2'
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PANEL_HTML = os.path.join(_PLUGIN_DIR, 'panel.html')
_BG_ASSETS_DIR = os.path.join(_PLUGIN_DIR, 'assets', 'bg')
_BG_ASSET_NAMES = frozenset({'day.jpg', 'night.jpg'})


async def _serve_bg_asset(request):
    name = request.path.rsplit('/', 1)[-1]
    if name not in _BG_ASSET_NAMES:
        raise web.HTTPNotFound()
    path = os.path.join(_BG_ASSETS_DIR, name)
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        'Cache-Control': 'no-cache',
        'Content-Type': 'image/jpeg',
    })


for _bg_name in sorted(_BG_ASSET_NAMES):
    register_route(
        'GET',
        f'/api/ext/qq_music/assets/bg/{_bg_name}',
        _serve_bg_asset,
        auth=False,
    )


async def _json_body(request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@register_route('GET', '/api/ext/qq_music/stats', auth=True)
async def api_stats(request):
    store = get_store()
    await store.sync_groups_meta()
    return web.json_response({'ok': True, 'data': store.dashboard()})


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
    from plugins.qq_music.sources import get_source, list_sources

    store = get_store()
    sid = store.get_music_source()
    custom = store.get_custom_source_url()
    src = get_source(sid, custom)
    return web.json_response({
        'ok': True,
        'show_lyrics': store.get_global_show_lyrics(),
        'music_source': src.id,
        'music_source_name': src.name,
        'music_source_note': src.note,
        'custom_source_url': custom,
        'sources': list_sources(custom),
    })


@register_route('GET', '/api/ext/qq_music/series', auth=True)
async def api_series(request):
    period = (request.query.get('period') or 'day').strip()
    data = get_store().series(period)
    return web.json_response({'ok': True, 'data': data})


@register_route('GET', '/api/ext/qq_music/user_settings', auth=True)
async def api_get_user_settings(request):
    data = get_store().dashboard()
    return web.json_response({'ok': True, 'data': {'users': data.get('users') or []}})


@register_route('GET', '/api/ext/qq_music/group_settings', auth=True)
async def api_get_group_settings(request):
    store = get_store()
    await store.sync_groups_meta()
    data = store.dashboard()
    return web.json_response({'ok': True, 'data': {'groups': data.get('groups') or []}})


@register_route('POST', '/api/ext/qq_music/settings', auth=True)
async def api_post_settings(request):
    from plugins.qq_music.sources import get_source, list_sources, resolve_source

    body = await _json_body(request)
    store = get_store()
    if 'custom_source_url' in body:
        store.set_custom_source_url(str(body.get('custom_source_url') or ''))
    if 'music_source' in body:
        raw = str(body.get('music_source') or '').strip()
        src = resolve_source(raw, store.get_custom_source_url())
        if not src:
            return web.json_response({'ok': False, 'error': f'未知歌源：{raw}'}, status=400)
        store.set_music_source(src.id)
    if 'show_lyrics' in body:
        store.set_global_show_lyrics(bool(body.get('show_lyrics')))
    sid = store.get_music_source()
    custom = store.get_custom_source_url()
    src = get_source(sid, custom)
    return web.json_response({
        'ok': True,
        'show_lyrics': store.get_global_show_lyrics(),
        'music_source': src.id,
        'music_source_name': src.name,
        'music_source_note': src.note,
        'custom_source_url': custom,
        'sources': list_sources(custom),
    })


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
    blob = store._users().get(user_key, {})
    return web.json_response({
        'ok': True,
        'show_lyrics': blob.get('show_lyrics'),
        'effective_show_lyrics': effective,
    })


@register_route('POST', '/api/ext/qq_music/group_settings', auth=True)
async def api_post_group_settings(request):
    from plugins.qq_music.sources import known_source_ids, resolve_source

    body = await _json_body(request)
    gid = str(body.get('group_id') or '').strip()
    if not gid:
        return web.json_response({'ok': False, 'error': 'group_id 不能为空'}, status=400)
    store = get_store()
    name = str(body.get('name') or '')
    store.touch_group(gid, name)
    if 'music_source' in body:
        sid = str(body.get('music_source') or '').strip()
        if sid and sid not in ('follow', 'global', '跟随', '跟随全局'):
            custom = store.get_custom_source_url()
            if sid not in known_source_ids(custom) and not resolve_source(sid, custom):
                return web.json_response({'ok': False, 'error': f'未知歌源：{sid}'}, status=400)
        store.set_music_source(sid or 'follow', gid)
    if 'show_lyrics' in body:
        raw = body.get('show_lyrics')
        if raw is None:
            effective = store.set_group_show_lyrics(gid, None, name)
        else:
            effective = store.set_group_show_lyrics(gid, bool(raw), name)
    else:
        effective = store.should_show_lyrics('', '', gid)
    blob = store._groups().get(gid, {})
    return web.json_response({
        'ok': True,
        'show_lyrics': blob.get('show_lyrics'),
        'effective_show_lyrics': effective,
        'music_source': blob.get('music_source') or '',
    })


@register_route('GET', '/api/ext/qq_music/group_remark', auth=True)
async def api_get_group_remark(request):
    gid = str(request.query.get('group_id') or '').strip()
    if not gid:
        return web.json_response({'ok': False, 'error': 'group_id 不能为空'}, status=400)
    store = get_store()
    data = dict(store.get_group_remark(gid))
    blob = store._groups().get(gid, {})
    if isinstance(blob, dict):
        av = str(blob.get('avatar') or '').strip()
        if av:
            data['avatar'] = av
    return web.json_response({'ok': True, 'data': data})


@register_route('POST', '/api/ext/qq_music/group_remark', auth=True)
async def api_post_group_remark(request):
    body = await _json_body(request)
    gid = str(body.get('group_id') or '').strip()
    if not gid:
        return web.json_response({'ok': False, 'error': 'group_id 不能为空'}, status=400)
    store = get_store()
    name = str(body.get('name') or body.get('remark') or '')
    qq = str(body.get('qq') or body.get('group_qq') or '')
    data = store.set_group_remark(gid, name, qq)
    await store.sync_groups_meta(max_api=4)
    for g in store.dashboard().get('groups') or []:
        if g.get('group_id') == gid:
            data = dict(g)
            break
    return web.json_response({'ok': True, 'data': data})


# 页面注册/注销已移至 main.py 的 @on_load / @on_unload（与主动推送、工作流API 一致）