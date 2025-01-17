import asyncio
import datetime
import httpx
import os
import random
import string
import time
import uuid
import urllib.parse

from sanic import Sanic, Request
from sanic.log import logger
from sanic.response import json, html, text, redirect

import tortoise.exceptions
from twitchAPI.twitch import Twitch
from twitchAPI.helper import first

import stream_emotes.db
from stream_emotes.db.models.user import User
from stream_emotes.db.models.oauth import OAuthState, OAuthBearer
from stream_emotes.db.models.emote import Emote, UserEmote
from stream_emotes import twitchua

APP = Sanic("twitchemotes-server")

def generate_password(length: int):
    return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

@APP.before_server_start
async def init(_app: Sanic):
    await stream_emotes.db.init()

@APP.get('/')
async def login(_req: Request):
    # Save the state for later verification
    state = await OAuthState.create(state=generate_password(32), expires_at=datetime.datetime.now() + datetime.timedelta(minutes=5))

    # Redirect to twitch
    params = {
        'client_id': os.environ['TWITCH_APP_ID'],
        'redirect_uri': os.environ['TWITCH_APP_REDIRECT_URI'],
        'response_type': 'code',
        'scope': 'user:read:emotes user:read:email',
        'state': state.state
    }
    params_string = urllib.parse.urlencode(params, doseq=True)
    return redirect(f'https://id.twitch.tv/oauth2/authorize?{params_string}')

@APP.get('/redirect')
async def handle_redirect(req: Request):
    state = req.args.get('state')
    if not state:
        return text('Missing state', status=400)

    try:
        await OAuthState.get(state=state)
    except tortoise.exceptions.DoesNotExist:
        return text('Invalid state', status=400)

    scope = req.args.get('scope')
    if not scope:
        return text('missing scope', status=400)

    code = req.args.get('code')
    if not code:
        return text('missing code', status=400)

    async with httpx.AsyncClient() as client:
        # Use the code to get a token
        res = await client.post(
            'https://id.twitch.tv/oauth2/token',
            params={
                'client_id': os.environ['TWITCH_APP_ID'],
                'client_secret': os.environ['TWITCH_APP_SECRET'],
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': os.environ['TWITCH_APP_REDIRECT_URI'],
            }
        )
        if res.status_code >= 400:
            logger.warning("Error getting token: %s", res.json())
            return text('Error getting token', status=res.status_code)

        token = res.json()

        # Use the token to get twitch user info

        res = await client.get(
            'https://api.twitch.tv/helix/users',
            headers={
                'Authorization': f'Bearer {token["access_token"]}',
                'Client-Id': os.environ['TWITCH_APP_ID'],
            }
        )
        res.raise_for_status()
        twitch_user = res.json()

    # Save the new Twitch token
    try:
        bearer = await OAuthBearer.get(twitch_id=twitch_user['data'][0]['id'])
        bearer.access_token = token['access_token']
        bearer.refresh_token = token['refresh_token']
        bearer.expires_at = datetime.datetime.now() + datetime.timedelta(seconds=token['expires_in'])
    except tortoise.exceptions.DoesNotExist:
        bearer = OAuthBearer(
            twitch_id=twitch_user['data'][0]['id'],
            login=twitch_user['data'][0]['login'],
            display_name=twitch_user['data'][0]['display_name'],
            access_token=token['access_token'],
            refresh_token=token['refresh_token'],
            expires_at=datetime.datetime.now() + datetime.timedelta(seconds=token['expires_in'])
        )
    await bearer.save()

    # Give our user some temporary credentials so they can prove they are the ones setting the minecraft username in the follow-up query
    try:
        user = await User.get(
            twitch_id=bearer.twitch_id
        )
        user.oauth_bearer = bearer
    except tortoise.exceptions.DoesNotExist:
        user = User(
            twitch_id=bearer.twitch_id,
            oauth_bearer=bearer
        )
    user.temp_token = generate_password(256)
    user.temp_token_expires_at = datetime.datetime.now() + datetime.timedelta(minutes=5)
    await user.save()

    return html(f'''
        <form method="POST" action="/set-username">
            <label for="username">Minecraft username: </label><input type="text" id="username" name="username" placeholder="Username">
            <input type="hidden" name="temp-token" value="{user.temp_token}">
            <input type="submit">
        </form>
    ''')

EMOTES_LOCK = asyncio.Lock()
EMOTES_LOCKS = {}

@APP.post('/set-username')
async def set_username(req: Request):
    logger.info('Trying to set username')

    username = req.form.get('username')
    temp_token = req.form.get('temp-token')

    if not username or not temp_token:
        return text('Missing form data', status=400)

    async with httpx.AsyncClient() as client:
        res = await client.get(f'https://playerdb.co/api/player/minecraft/{username.lower()}') # for some reason I get a 500 on a non-lowered username
        logger.info('GOT SHIT')
        if res.status_code >= 400 and res.status_code < 500:
            return text('Invalid username', status=400)
        if res.status_code >= 500:
            logger.warning('Failed getting MC UUID for %s: %s', username, res.text)
            res.raise_for_status()

        minecraft_uuid = uuid.UUID(res.json()['data']['player']['raw_id'])

    user = await User.get(temp_token=temp_token, temp_token_expires_at__gte=datetime.datetime.now())
    user.minecraft_uuid = minecraft_uuid
    await user.save()

    async with EMOTES_LOCK:
        EMOTES_LOCKS.setdefault(minecraft_uuid, asyncio.Lock())

    async with EMOTES_LOCKS[minecraft_uuid]:
        await fetch_user_emotes(user, minecraft_uuid)

    return text('Success! You can close this now :)')

async def fetch_user_emotes(user: User, req_uuid: uuid.UUID):
    logger.info(f'Fetching user emotes for {req_uuid}')

    emotes = await twitchua.request(
        'get',
        f'helix/chat/emotes/user',
        await user.oauth_bearer,
        {'user_id': (await user.oauth_bearer).twitch_id}
    )
    logger.info('GOT %d EMOTES FROM TWITCH', len(emotes['data']))

    emote_objects = []
    for emote in emotes['data']:
        animated = 'animated' in emote['format']
        url = emotes['template'] \
            .replace('{{id}}', emote['id']) \
            .replace('{{format}}', 'animated' if animated else 'static') \
            .replace('{{theme_mode}}', 'dark') \
            .replace('{{scale}}', emote['scale'][-1])

        emote_object = Emote(
            id=emote['id'],
            name=emote['name'],
            animated=animated,
            url=url
        )
        emote_objects.append(emote_object)

    await Emote.bulk_create(emote_objects, on_conflict=['name'], update_fields=['animated', 'url'])

    logger.info('Saved emotes')

    await tortoise.connections.get('default').execute_many(
        'INSERT INTO "useremote" ("user_id", "emote_id") VALUES ($1, $2) ON CONFLICT DO NOTHING;',
        [
            [user.id, emote.id]
            for emote in emote_objects
        ]
    )

    logger.info('Saved emote associations')

    user.last_emote_fetch = datetime.datetime.now()
    await user.save()

    logger.info('Done fetching user emotes')

@APP.get('/v1/emotes')
async def get_all_emotes(_req: Request):
    emotes = await Emote.all()

    return json([
        {
            'id': emote.id,
            'name': emote.name,
            'animated': emote.animated,
            'url': emote.url
        }
        for emote in emotes
    ])

@APP.get('/v1/emotes/<req_uuid>')
async def get_emotes(req: Request, req_uuid: str):
    req_uuid = uuid.UUID(req_uuid)

    try:
        user = await User.get(minecraft_uuid=req_uuid).prefetch_related()
    except tortoise.exceptions.DoesNotExist:
        return json([], 404)

    async with EMOTES_LOCK:
        EMOTES_LOCKS.setdefault(req_uuid, asyncio.Lock())

    async with EMOTES_LOCKS[req_uuid]:
        if req.args.get('forcerefresh') or user.last_emote_fetch is None or datetime.datetime.now(datetime.timezone.utc) - user.last_emote_fetch > datetime.timedelta(days=1):
            # It's been a while (or never), let's get that user's emotes!
            # We don't actually wait for this, because it can take a while and if the client has to
            # wait too long to get emotes, it causes issues (messages not showing up and/or
            # disconnections due to message verification issues)
            #
            # These should probably be fixed client-side tbh... But I'm lazy.
            asyncio.ensure_future(fetch_user_emotes(user, req_uuid))

    user_emotes = await UserEmote.filter(user=user).prefetch_related('emote')

    out = []
    for emote in user_emotes:
        emote_ = await emote.emote
        out.append({
            'id': emote_.id,
            'name': emote_.name,
            'animated': emote_.animated,
            'url': emote_.url
        })
    return json(out)
