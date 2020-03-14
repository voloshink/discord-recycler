import discord
from configparser import ConfigParser
import redis
from urlextract import URLExtract
import requests
from urllib.parse import urlparse
from io import BytesIO
from PIL import Image
import imagehash
import datetime
import dateutil.relativedelta
import sys

CONFIG_FILE = "config.ini"


class Recycler(discord.Client):
    def __init__(self, redis, config):
        super().__init__()
        self.extractor = URLExtract()
        self.redis = redis
        self.whitelist_set = "blacklist"
        self.link_set = "links-"
        self.image_set = "images-"
        self.counter_redis = "counter-"
        self.config = config
        self.start_date = datetime.datetime.now()
        self.load_from_redis()

    def load_from_redis(self):
        whitelist = self.redis.smembers(self.whitelist_set)

        for host in whitelist:
            whitelist.remove(host)
            whitelist.add(host.decode("utf-8"))
        self.whitelist = whitelist

    async def on_ready(self):
        print('Logged on as {0}!'.format(self.user))

    async def on_message(self, message):
        guild = "0"
        if not message.guild == None:
            guild = str(message.guild.id)

        # Don't track bots
        if message.author.bot:
            return

        # Look for admin commands
        if message.author.id == int(config.get('bot', 'admin')):
            if message.content.startswith('!r'):
                await self.handle_command(message)

        links = self.extract_links(message.content)

        # Easier to treat attachmenets just like links
        for attachment in message.attachments:
            links.append(attachment.url)

        # Get link content and type since url extensions can't be trusted
        link_contents = {}
        for link in links:
            try:
                link_contents[link] = requests.get(link)
            except:
                print("error getting link")

        # Check if link is duplicate
        link_duplicate_map = {}
        for link in link_contents:
            print(link, link_contents[link].headers['content-type'])
            print("link", self.is_link_duplicate(link, guild))
            link_duplicate_map[link] = self.is_link_duplicate(
                link, guild)

        for link in link_duplicate_map:
            if link_duplicate_map[link]:
                if config.get('bot', 'ignoregifs') == 1 and link_contents[link].headers['content-type'] == "image/gif":
                    link_duplicate_map[link] = False
                    continue

            if link_contents[link].headers['content-type'].startswith('image/'):
                try:
                    image = Image.open(
                        BytesIO(link_contents[link].content))
                    hash = str(imagehash.average_hash(image))
                    if image.height < 200 and image.width < 200:
                        link_duplicate_map[link] = False
                    elif not link_duplicate_map[link]:
                        print("Image dupe",
                              self.is_image_duplicate(hash, guild))
                        link_duplicate_map[link] = self.is_image_duplicate(
                            hash, guild)
                    self.save_image(hash, guild)
                except:
                    print("error opening image")
                    continue

        if any(link_duplicate_map.values()):
            await self.recycle_message(message, guild)

        # separate loop to ensure multiple duplicate links in the same message aren't counted as reposts
        for link in link_duplicate_map:
            if not link_duplicate_map[link]:
                self.save_link(link, guild)

    def is_link_duplicate(self, link, guild):
        parsed = urlparse(link)
        if parsed.hostname in self.whitelist:
            return False
        return self.redis.sismember(self.link_set + guild, link)

    def is_image_duplicate(self, image, guild):
        return self.redis.sismember(self.image_set + guild, image)

    def save_link(self, link, guild):
        self.redis.sadd(self.link_set + guild, link)

    def save_image(self, image, guild):
        self.redis.sadd(self.image_set + guild, image)

    async def recycle_message(self, message, guild):
        try:
            await message.add_reaction("♻️")
        except discord.Forbidden:
            await message.channel.send(content="♻️")
        except:
            pass

        self.redis.incr(self.counter_redis + guild)

    def extract_links(self, text):
        urls = self.extractor.find_urls(text)
        return urls

    async def handle_command(self, message):
        command = message.content
        if command == "!rstats":
            await self.post_stats(message)
        if command == "!rkill":
            sys.exit()

    async def post_stats(self, message):
        guild = "0"
        if not message.guild is None:
            guild = str(message.guild.id)

        stats = self.get_stats(guild)
        embed = discord.Embed(colour=discord.Colour(0x86ff00))
        embed.set_author(
            name="Recycler", icon_url="https://emojipedia-us.s3.dualstack.us-west-1.amazonaws.com/thumbs/120/apple/237/black-universal-recycling-symbol_267b.png")

        embed.add_field(name="Uptime", value=self.get_uptime())
        embed.add_field(name="Messages Recycled", value=stats['recycles'])
        embed.add_field(name="Links in Database",
                        value=stats['links'])
        embed.add_field(name="Image Hashes in Database",
                        value=stats['images'])

        await message.channel.send(content="Bot Stats", embed=embed)

    def get_uptime(self):
        now = datetime.datetime.now()
        uptime_string = ""
        delta = dateutil.relativedelta.relativedelta(now, self.start_date)
        if delta.years > 0:
            uptime_string += ' {} years '.format(delta.years)
        if delta.months > 0:
            uptime_string += ' {} months '.format(delta.months)
        if delta.days > 0:
            uptime_string += ' {} days '.format(delta.days)
        if delta.hours > 0:
            uptime_string += ' {} hours '.format(delta.hours)
        if delta.minutes > 0:
            uptime_string += ' {} minutes '.format(delta.minutes)
        if delta.seconds > 0:
            uptime_string += ' {} seconds '.format(delta.seconds)
        return uptime_string.strip()

    def get_stats(self, guild):
        stats = {}
        stats['links'] = self.redis.scard(self.link_set + guild)
        stats['images'] = self.redis.scard(self.image_set + guild)
        stats['recycles'] = self.redis.get(
            self.counter_redis + guild).decode('utf-8')
        return stats


config = ConfigParser()
config.read(CONFIG_FILE)
token = config.get('discord', 'token')
redis_password = config.get('redis', 'password')

r = redis.Redis(host='localhost', port=6379, password=redis_password)

client = Recycler(r, config)
client.run(token)
