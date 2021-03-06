from fastapi import Depends, FastAPI, Query, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from urllib.request import Request, urlopen
from pathlib import Path
from os import getenv, name
from io import BytesIO
from json import loads
from json.decoder import JSONDecodeError
from base64 import b64decode, binascii
from typing import Callable
from math import ceil

from renders import *

class ImageSizeError(Exception):
    pass
class UrlError(Exception):
    pass
class ArgumentError(Exception):
    pass
class UnknownPlayerError(Exception):
    pass

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

def skin_url_from_player(player_name: str) -> [str, bool]:
    """Get a player's skin URL (And skin model).

    Args:
        player_name (String): Minecraft In-Game Name.

    Raises:
        UnknownPlayerError: Raised when no played with the input name is found.

    Returns:
        String, Boolean: Skin URL, Whether the skin uses the slim body type.
    """
    try:
        id_json = loads(urlopen('https://api.mojang.com/users/profiles/minecraft/' + player_name).read())
    except JSONDecodeError:
        raise UnknownPlayerError
    if not id_json:
        raise UnknownPlayerError
    data_json = loads(urlopen('https://sessionserver.mojang.com/session/minecraft/profile/' + id_json['id']).read())
    data = data_json['properties'][0]['value']
    decoded_json = loads(b64decode(data).decode('utf-8'))

    link = decoded_json['textures']['SKIN']['url']
    try:
        slim = decoded_json['textures']['SKIN']['metadata']['model'] == 'slim'
    except KeyError:
        slim = False
    return link, slim

def skin_from_url(skin_url: str) -> Image:
    """Get an image object from a minecraft skin url
    Args:
        skin_url (String): A Minecraft Player's skin URL.
    Raises:
        UrlError: Raised on any URL errors.
        ImageSizeError: Raised when the image isn't the correct size of a Minecraft skin.
    Returns:
        Image: The minecraft skin as an Image object.
    """
    try:
        req = Request(url=skin_url, headers={'User-Agent':' Mozilla/5.0 (Windows NT 6.1; WOW64; rv:12.0) Gecko/20100101 Firefox/12.0'})
        file = urlopen(req)
    except ValueError:
        raise UrlError
    try:
        image = Image.open(file).convert('RGBA')
    except UnidentifiedImageError:
        raise UrlError
    if image.size == (64, 32):
        image = old_to_new_skin(image)
    if image.size != (64, 64):
        raise ImageSizeError
    return image

async def api_template(render_function: Callable, path: str, player: str, skin_url: str, skin_base64: str, slim: bool, overlay: bool, margin: int, upscale: int) -> StreamingResponse:
    """Template for every API, for easy access.

    Args:
        render_function (function): Which function to process skin images through.
        path (str): What folder to cache processed images in. (after '/tmp/')
        player_name (str, optional): What Minecraft Player to get the skin image from.
        skin_url (str, optional): What URL to get the skin image from.
        skin_base64 (str, optional): Base64 hash to decode for the skin image.
        slim (bool, optional): Whether the skin uses the slim body type.
        overlay (bool, optional): Whether or not to show the skin's overlay layers.
        margin (bool): How many pixels around the image to make transparent. (1 to 8)
        upscale (bool): How many times to increase the size of a pixel. (2 to 8)

    Returns:
        StreamingResponse: Processed image object as a FastAPI response object.
    """
    if player:
        try:
            url, pot_slim = skin_url_from_player(player)
            filename = url.split('/')[-1][-16:-1]
        except UnknownPlayerError:
            return HTTPException(status_code=404, detail="Unknown player")
    elif skin_url:
        filename = skin_url.split('/')[-1][-16:-1]
    elif skin_base64:
        filename = skin_base64[-16:-1]
    else:
        return HTTPException(status_code=404, detail="You must specify a Player Name, Skin URL or Skin File.")

    # modifier file names
    filename = filename.rsplit('.', 1)[0]

    if overlay:
        filename += 'o'
    if margin > 0:
        filename += 'm' + str(margin)
    if upscale > 1:
        filename += 'u' + str(upscale)

    filename += '.png'

    # really weird hack to make it compatible with both windows and linux file systems
    # in reality it's Path("/tmp")
    pot_path = Path(f'{"/" if name != "nt" else ""}tmp') / path
    pot_file = pot_path / filename
    if Path(pot_file).is_file():
        image = Image.open(pot_file)
    else:
        if player:
            try:
                try:
                    skin = skin_from_url(url)
                except UrlError:
                    return HTTPException(status_code=404, detail="Invalid URL")
                except ImageSizeError:
                    return HTTPException(status_code=404, detail="Image must be 64x64 pixels large")
                if slim == None:
                    slim = pot_slim
            except UnknownPlayerError:
                return HTTPException(status_code=404, detail="Unknown player")
        elif skin_url:
            try:
                skin = skin_from_url(skin_url)
            except UrlError:
                return HTTPException(status_code=404, detail="Invalid URL")
            except ImageSizeError:
                return HTTPException(status_code=404, detail="Image must be 64x64 pixels large")
        elif skin_base64:
            try:
                b = BytesIO(b64decode(skin_base64 + '='))
            except binascii.Error:
                b = BytesIO(b64decode(skin_base64))
            try:
                skin = Image.open(b)
            except UrlError:
                return HTTPException(status_code=404, detail="Invalid File, must be Image")

        image = render_function(skin, slim, overlay)
        if margin > 0:
            new = Image.new('RGBA', (image.width+margin+margin, image.height+margin+margin))
            new.paste(image, (margin, margin))
            image = new
        if upscale > 1:
            image = image.resize((image.width*upscale, image.height*upscale), Image.NEAREST)

        try:
            image.save(pot_file)
        except FileNotFoundError:
            Path(pot_path).mkdir()
            image.save(pot_file)

    # post-processing, eventually

    byte_result = BytesIO()
    image.save(byte_result, format='PNG')
    byte_result.seek(0)
    return StreamingResponse(byte_result, media_type="image/png")

def template_args(player_name: str = Query(None, max_length=16, description="What Minecraft Player to get the skin image from."),
                  skin_url: str = Query(None, max_length=128, description="What URL to get the skin image from."),
                  skin_base64: str = Query(None, max_length=16*1024, description="Base64 hash to decode for the skin image."),
                  slim: bool = Query(False, description="Whether the skin uses the slim body type."),
                  overlay: bool = Query(True, description="Whether or not to show the skin's overlay layers."),
                  margin: int = Query(0, description="How many pixels around the image to make transparent. (1 to 8)", ge=1, le=8),
                  upscale: int = Query(1, description="How many times to increase the size of a pixel. (2 to 8)", ge=2, le=8)
                  ):
    return [player_name, skin_url, skin_base64, slim, overlay, margin, upscale]

@app.get("/")
async def landing():
    return FileResponse('static/index.html')

@app.get('/skin/')
async def skin(args: template_args = Depends()):
    return await api_template(lambda x, *_: x, 'skins/', *args)

@app.get('/portrait/')
async def portrait(args: template_args = Depends()):
    return await api_template(skin_to_portrait, 'portraits/' , *args)

@app.get('/face/')
async def face(args: template_args = Depends()):
    return await api_template(skin_to_face, 'profiles/', *args)