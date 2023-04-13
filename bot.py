import asyncio
import discord
from discord.ext import commands
from discord.ext.commands import Greedy, Context  # or a subclass of yours
from typing import Optional, Literal
import aiohttp
import pdfplumber
import secrets
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_KEY = os.environ.get("OPENAI_KEY")
GUILD_ID = os.environ.get("GUILD_ID")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.guild_messages = True

bot = commands.Bot(command_prefix="$", intents=intents)


@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        print("Bot sent a message")
    else:
        print("Message from {0.author}: {0.content}".format(message))

    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(
            f"⌛ {ctx.message.author.mention} {round(error.retry_after, 2)} seconds left before you can use this command again."
        )


# Remove the default help command
bot.remove_command("help")


# Define a custom help command
@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="Help",
        description="List of commands for the bot",
        color=discord.Color.blue(),
    )

    # Add fields for each command you want to display
    embed.add_field(
        name="$review",
        value="Attach a PDF of your resume and the bot will give you feedback on how to improve it.",
        inline=False,
    )
    embed.add_field(
        name="$revise",
        value="Send the bot a list of bullet points and it will revise them for you.",
        inline=False,
    )

    await ctx.send(embed=embed)


@bot.command()
@commands.guild_only()
@commands.is_owner()
async def sync(
    ctx: Context,
    guilds: Greedy[discord.Object],
    spec: Optional[Literal["~", "*", "^"]] = None,
) -> None:
    if not guilds:
        if spec == "~":
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "*":
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
        elif spec == "^":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
            await ctx.bot.tree.sync(guild=ctx.guild)
            synced = []
        else:
            synced = await ctx.bot.tree.sync()

        await ctx.send(
            f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}"
        )
        return

    ret = 0
    for guild in guilds:
        try:
            await ctx.bot.tree.sync(guild=guild)
        except discord.HTTPException:
            pass
        else:
            ret += 1

    await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")


async def download_file(url, headers=None, filepath="resume.pdf"):
    """Download a file from a URL to a local file.

    Args:
        url (str): The URL to download from.
        filepath (str, optional): Path to save file to. Defaults to "resume.pdf".

    Raises:
        Exception: If the response status is not 200.
    """
    print("📥 Downloading file...")
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            if response.status == 200:
                with open(filepath, "wb") as f:
                    while True:
                        chunk = await response.content.read(1024)
                        if not chunk:
                            break
                        f.write(chunk)
            else:
                raise Exception(f"Error downloading file: {response.status}")
    print("✅ File downloaded")


async def gptHandleResume(resume_text: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": OPENAI_KEY,
    }
    print("🤖 Calling GPT...")
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(
            "https://api.openai.com/v1/chat/completions",
            json={
                "messages": [
                    {
                        "role": "system",
                        "content": "You're a recruiter helping candidates improve their resumes:\n###\n"
                        + resume_text
                        + "\n###\nPlease provide concise feedback and actionable improvements to this resume, citing specific examples.\n"
                        + "Rules: Do not recommend adding a career summary at the top. Do not nitpick on spacing. Do not worry about formatting or styles. Don't worry about multiple roles at the same company. Only return a numbered list of actionable improvements, no additional text.",
                    },
                ],
                "model": "gpt-4",
                "max_tokens": 350,
            },
        ) as response:
            if response.status == 200:
                print("✅ GPT call successful")
                data = await response.json()
                feedback = data["choices"][0]["message"]["content"]
                print(feedback)
                return feedback


async def handleTextExtraction(ctx, file_path: str):
    resume_text = ""
    with pdfplumber.open(file_path) as pdf:
        # Convert the PDF to text
        if len(pdf.pages) > 1:
            await ctx.send(
                "⚠️ Your resume has multiple pages. Only the first page will be scored. Two page resumes are not recommended."
            )

        print("🏭 Extracting text...")
        resume_text += pdf.pages[0].extract_text(
            x_tolerance=1, y_tolerance=1, layout=True
        )
        print("✅ Extracted text...")

        with open(f"{file_path.split('.')[0]}.txt", "wb") as resumeTextFile:
            resumeTextFile.write(resume_text.encode("utf-8"))

        return resume_text


@bot.command(name="review")
@commands.cooldown(1, 60 * 2, commands.BucketType.user)
async def reviewResume(ctx):
    if ctx.message.attachments:
        pendingMessage = await ctx.send(
            f"🤖 Processing your resume {ctx.message.author.mention}..."
        )

        attachment = ctx.message.attachments[0]
        if attachment.filename.endswith(".pdf"):
            # Download the file
            file_name = secrets.token_urlsafe(16) + ".pdf"

            # Check if the folder exists
            if not os.path.exists("files"):
                os.makedirs("files")

            file_path = f"files/{file_name}"
            await download_file(attachment.url, filepath=file_path)

            try:
                resume_text = await handleTextExtraction(ctx, file_path)
            except:
                await pendingMessage.edit(
                    content=f"⚠️ There was an error processing your resume {ctx.message.author.mention}. <@129678295057956864> will look into it."
                )
                return

            print("🤖 Fetching GPT-4 response")
            try:
                feedback = await gptHandleResume(resume_text)
                await pendingMessage.edit(
                    content=f"🤖 Here is your resume feedback {ctx.message.author.mention}:\n\n{feedback}"
                )
            except:
                await pendingMessage.edit(
                    content=f"⚠️ There was an error processing your resume {ctx.message.author.mention}. <@129678295057956864> will look into it."
                )
        else:
            await ctx.send(
                f"{ctx.message.author.mention}, please send a PDF file of your resume."
            )
    else:
        await ctx.send(
            f"{ctx.message.author.mention}, please send a PDF file of your resume."
        )


async def gptHandleBullets(bullets):
    headers = {
        "Content-Type": "application/json",
        "Authorization": OPENAI_KEY,
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(
            "https://api.openai.com/v1/chat/completions",
            json={
                "messages": [
                    {
                        "role": "system",
                        "content": "Help revise these bullet points for a resume:\n###\n"
                        + bullets
                        + "Rules: Return only a revised copy of each bullet point if necessary.",
                    },
                ],
                "model": "gpt-4",
                "max_tokens": 350,
            },
        ) as response:
            if response.status == 200:
                print("✅ GPT call successful")
                data = await response.json()
                feedback = data["choices"][0]["message"]["content"]
                print(feedback)
            return feedback


@bot.command(name="revise")
@commands.cooldown(1, 30, commands.BucketType.user)
async def reviseBullets(ctx, *, bullets):
    if ctx.message is None:
        return

    pendingMessage = await ctx.send(
        f"🤖 Thinking of revisions {ctx.message.author.mention}..."
    )

    print("🤖 Fetching GPT-4 response")
    try:
        feedback = await gptHandleBullets(bullets)
        print("✅ GPT-4 response fetched", feedback)
        await pendingMessage.edit(
            content=f"🤖 Here are your revised bullets {ctx.author.mention}:\n\n{feedback}"
        )
    except:
        await pendingMessage.edit(
            content=f"⚠️ There was an error processing your bullets {ctx.message.author.mention}. <@129678295057956864> will look into it."
        )
        print("⚠️ There was an error processing the bullets.")


async def main():
    async with bot:
        # do you setup stuff if you need it here, then:
        bot.tree.copy_global_to(
            guild=discord.Object(id=GUILD_ID)
        )  # we copy the global commands we have to a guild, this is optional
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
