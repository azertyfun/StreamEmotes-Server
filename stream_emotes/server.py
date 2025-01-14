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
            print(res.json())
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
    bearer, _ = await OAuthBearer.update_or_create(
        twitch_id=twitch_user['data'][0]['id'],
        login=twitch_user['data'][0]['login'],
        display_name=twitch_user['data'][0]['display_name'],
        access_token=token['access_token'],
        refresh_token=token['refresh_token'],
        expires_at=datetime.datetime.now() + datetime.timedelta(seconds=token['expires_in'])
    )

    # Give our user some temporary credentials so they can prove they are the ones setting the minecraft username in the follow-up query
    user, _ = await User.update_or_create(
        temp_token=generate_password(256),
        temp_token_expires_at=datetime.datetime.now() + datetime.timedelta(minutes=5),
        oauth_bearer=bearer
    )

    print(user)

    return html(f'''
        <form method="POST" action="/set-username">
            <label for="username">Username: </label><input type="text" id="username" name="username" placeholder="Username">
            <input type="hidden" name="temp-token" value="{user.temp_token}">
            <input type="submit">
        </form>
    ''')

@APP.post('/set-username')
async def set_username(req: Request):
    username = req.form.get('username')
    temp_token = req.form.get('temp-token')

    if not username or not temp_token:
        return text('Missing form data', status=400)

    async with httpx.AsyncClient() as client:
        res = await client.get(f'https://playerdb.co/api/player/minecraft/{username}')
        if res.status_code >= 400 and res.status_code < 500:
            return text('Invalid username', status=400)
        res.raise_for_status()

        minecraft_uuid = uuid.UUID(res.json()['data']['player']['raw_id'])

    user = await User.get(temp_token=temp_token, temp_token_expires_at__gte=datetime.datetime.now())
    user.minecraft_uuid = minecraft_uuid
    await user.save()

    return text('Success! You can close this now :)')

EMOTES_LOCK = asyncio.Lock()
EMOTES_LOCKS = {}

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
        if req.args.get('forcerefresh') or user.last_emote_fetch is None or datetime.datetime.now(datetime.timezone.utc) - user.last_emote_fetch > datetime.timedelta(hours=1):
            # It's been a while (or never), let's get that user's emotes!
            emotes = await twitchua.request(
                'get',
                f'helix/chat/emotes/user',
                await user.oauth_bearer,
                {'user_id': (await user.oauth_bearer).twitch_id}
            )

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

                await emote_object.save(force_update=True) # JFC I hate ORMs. Hate hate hate. CAN'T refer to the foreign key without using a .save()'d object. SO THE FUCKING BULK_CREATE FUNCTION SERVES NO FUCKING PURPOSE.

            # await Emote.bulk_create(emote_objects, on_conflict=['name'], update_fields=['animated', 'url'])

            user_emote_objects = [
                UserEmote(
                    user=user,
                    emote=emote
                )
                for emote in emote_objects
            ]
            await UserEmote.bulk_create(user_emote_objects, ignore_conflicts=True)

            user.last_emote_fetch = datetime.datetime.now()
            await user.save()

    user_emotes = await UserEmote.filter(user=user).prefetch_related()

    return json([
        {
            'id': (await emote.emote).id,
            'name': (await emote.emote).name,
            'animated': (await emote.emote).animated,
            'url': (await emote.emote).url
        }
        for emote in user_emotes
    ])
