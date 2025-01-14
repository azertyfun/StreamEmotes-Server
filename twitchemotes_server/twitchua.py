import os

import httpx

from twitchemotes_server.db.models.oauth import OAuthBearer

async def request(method: str, path: str, bearer: OAuthBearer, params: dict = None,):
    params = params or {}
    async with httpx.AsyncClient() as client:
        res = await client.request(
            method, f'https://api.twitch.tv/{path}',
            headers={
                'Authorization': f'Bearer {bearer.access_token}',
                'Client-Id': os.environ['TWITCH_APP_ID'],
            },
            params=params
        )

        if res.status_code == 401:
            # Access Token Expired!
            res = await client.post(
                'https://id.twitch.tv/oauth2/token',
                data={
                    'client_id': os.environ['TWITCH_APP_ID'],
                    'client_secret': os.environ['TWITCH_APP_SECRET'],
                    'grant_type': 'refresh_token',
                    'refresh_token': bearer.refresh_token
                }
            )
            res.raise_for_status()
            new_token = res.json()
            bearer.access_token = new_token['access_token']
            bearer.refresh_token = new_token['refresh_token']
            await bearer.save()

            # Try again with new token
            res = await client.request(
                method, f'https://api.twitch.tv/{path}',
                headers={
                    'Authorization': f'Bearer {bearer.access_token}',
                    'Client-Id': os.environ['TWITCH_APP_ID'],
                },
                params=params
            )

        res.raise_for_status()
        j = res.json()

        if isinstance(j, dict) and j.get('data'):
            if j.get('pagination'):
                newparams = params.copy()
                newparams['after'] = j["pagination"]["cursor"]
                return {
                    'data': j['data'] + (await request(method, path, bearer, params=newparams))['data'],
                    **{
                        k: v
                        for k, v in j.items()
                        if k != 'data'
                    }
                }
            else:
                return j
        else:
            return j
