import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional
import os
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ------------ IDs / CONSTANTS ------------
MAIN_GUILD_ID = 1338455645896310784   # main server ID
APPEAL_GUILD_ID = 1497651620681486338 # appeal server ID
APPEAL_CHANNEL_ID = 1497668613283254312  # appeal review channel ID (in appeal server)
STAFF_ROLE_ID = 1497662209285689575     # staff role in appeal server (ping + permissions)

# ROLES IN MAIN SERVER
MOD_ROLE_ID = 1339202997208616990       # Mod role ID
TRIAL_MOD_ROLE_ID = 1374305296326856734 # Trial Mod role ID

# MAIN SERVER LOG CHANNEL (for ban/unban/false-ban/kick logs)
LOG_CHANNEL_ID = 1408472513108770816

# Channel for deleted-message logs (in main server)
DELETE_LOG_CHANNEL_ID = 1408474605844172910

MAIN_SERVER_INVITE = "https://discord.gg/mmml"
SERVER_NAME = "Monke Monke Monke League"
APPEAL_LINK = "https://discord.gg/Dn9N2GdGVT"  # for ban DM

# Rule book links
SERVER_RULES_LINK = "https://docs.google.com/document/d/12T179hGHc_CTRB1PUsrb62WjMGms6kF2IYAJHh9O5m8/edit?usp=drivesdk"
GAME_RULEBOOK_LINK = "https://docs.google.com/document/d/1207tu3VHGdRXVx7cIv2HkmYVkn4sV6Pkdoz26JAYAms/edit?usp=drivesdk"

# Words/phrases to auto-delete (case-insensitive)
BAD_WORDS = [
    "fuck", "bitch", "asshole", "bullshit", "bastard", "cock", "dammit", "dick",
    "dick head", "dickhead", "dumb ass", "dumbass", "fucker", "fucking", "goddamnit",
    "hell", "jack ass", "jackass", "motherfucker", "nigga", "pussy", "sisterfuck",
    "niggers", "pee pee", "Pee Pee", "PEE PEE", "penis", "penis", "balls", 
    "cocksucker", "retartd", "retarted", "shi", "dih", "rtrd", "nga", "stfu",
    "b1tch", "a$$", "freak",
]

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.dm_messages = True
intents.message_content = True  # enable in Developer Portal as well

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Shared helpers ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

EST = ZoneInfo("America/New_York")

def format_time(dt: datetime) -> str:
    dt = dt.astimezone(EST)
    return dt.strftime("%m/%d/%Y %I:%M %p EST")

def parse_duration(text: str) -> Optional[timedelta]:
    if not text:
        return None
    text = text.strip().lower()
    if text in ("perm", "permanent", "perma", "permban", "perm ban", "permanent ban"):
        return None
    num = ""
    unit = ""
    for ch in text:
        if ch.isdigit():
            num += ch
        elif ch.isalpha():
            unit += ch
        else:
            continue
    if not num:
        return None
    n = int(num)
    if unit in ("d", "day", "days"):
        return timedelta(days=n)
    if unit in ("h", "hr", "hour", "hours"):
        return timedelta(hours=n)
    if unit in ("m", "min", "mins", "minute", "minutes"):
        return timedelta(minutes=n)
    if unit in ("s", "sec", "secs", "second", "seconds"):
        return timedelta(seconds=n)
    if unit in ("mo", "month", "months"):
        return timedelta(days=30 * n)
    return None

def get_delete_log_channel() -> Optional[discord.TextChannel]:
    ch = bot.get_channel(DELETE_LOG_CHANNEL_ID)
    return ch if isinstance(ch, discord.TextChannel) else None

def format_remaining(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "Expired"
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)

async def setup_countdown(message: discord.Message, end_time: datetime):
    while True:
        now = now_utc()
        remaining = end_time - now
        if remaining.total_seconds() <= 0:
            try:
                embed = message.embeds[0]
                for i, field in enumerate(embed.fields):
                    if field.name.lower().startswith("duration"):
                        embed.set_field_at(i, name=embed.fields[i].name, value="Expired", inline=False)
                        break
                await message.edit(embed=embed)
            except Exception:
                pass
            break
        remaining_text = format_remaining(remaining)
        try:
            embed = message.embeds[0]
            for i, field in enumerate(embed.fields):
                if field.name.lower().startswith("duration"):
                    embed.set_field_at(i, name=embed.fields[i].name, value=remaining_text, inline=False)
                    break
            await message.edit(embed=embed)
        except Exception:
            break
        await asyncio.sleep(60)

# ---------- Suspicious detection helpers ----------

SUSPICIOUS_KEYWORDS = {
    "withdraw", "withdrawal", "promo", "promo code", "activate", "activation",
    "bonus", "rakeback", "deposit", "launch", "click here", "claim", "earn",
    "giveaway", "free", "crypto", "usdt", "btc", "ethereum", "metamask"
}
SUSPICIOUS_DOMAINS = {"tiny.cc", "bit.ly", "free-giveaway.example"}  # add domains
delete_images_always = False

def message_contains_suspicious_text(text: Optional[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    for kw in SUSPICIOUS_KEYWORDS:
        if kw in low:
            return True
    return False

def embeds_contain_suspicious(embed: discord.Embed) -> bool:
    if embed.title and message_contains_suspicious_text(embed.title):
        return True
    if embed.description and message_contains_suspicious_text(embed.description):
        return True
    if embed.author and getattr(embed.author, "name", None) and message_contains_suspicious_text(embed.author.name):
        return True
    for f in embed.fields:
        if message_contains_suspicious_text(f.name) or message_contains_suspicious_text(f.value):
            return True
    return False

def attachments_or_embeds_have_images(message: discord.Message) -> bool:
    if message.attachments:
        for att in message.attachments:
            if any(att.filename.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
                return True
    for e in message.embeds:
        if e.image or e.thumbnail:
            return True
    return False

def message_has_suspicious_link(message: discord.Message) -> bool:
    text = (message.content or "") + " "
    parts = text.split()
    for p in parts:
        if p.startswith("http://") or p.startswith("https://") or "." in p:
            for d in SUSPICIOUS_DOMAINS:
                if d in p:
                    return True
    for e in message.embeds:
        if getattr(e, "url", None):
            for d in SUSPICIOUS_DOMAINS:
                if d in e.url:
                    return True
    return False

# ---------- Permission helpers ----------

def is_mod_or_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if MOD_ROLE_ID:
        role = member.guild.get_role(MOD_ROLE_ID)
        if role and role in member.roles:
            return True
    return False

def can_timeout(member: discord.Member) -> bool:
    if is_mod_or_admin(member):
        return True
    if TRIAL_MOD_ROLE_ID:
        trial_role = member.guild.get_role(TRIAL_MOD_ROLE_ID)
        if trial_role and trial_role in member.roles:
            return True
    return False

# ---------- Global state ----------
appeal_history: Dict[int, List[datetime]] = {}
active_appeals: Dict[int, int] = {}
pending_appeal_queue: List[int] = []

permanent_bans: set[int] = set()
earliest_appeal_time: Dict[int, datetime] = {}
temp_bans: Dict[int, datetime] = {}

case_counter = 1
def get_next_case_id() -> int:
    global case_counter
    cid = case_counter
    case_counter += 1
    return cid

def get_log_channel() -> Optional[discord.TextChannel]:
    ch = bot.get_channel(LOG_CHANNEL_ID)
    return ch if isinstance(ch, discord.TextChannel) else None

# ============================================================
#                       /submit-report  (kept unchanged)
# ============================================================

class SRActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Ban", value="ban"),
            discord.SelectOption(label="Warning", value="warning"),
            discord.SelectOption(label="Mute/Timeout", value="mute"),
        ]
        super().__init__(placeholder="Choose an action...", min_values=1, max_values=1, options=options, custom_id="sr_action_select")

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        view = SRMemberSelectView(action)
        await interaction.response.edit_message(content=f"Action selected: **{action.capitalize()}**. Now choose a member:", view=view)

class SRActionSelectView(discord.ui.View):
    def __init__(self, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.add_item(SRActionSelect())

class SRMemberSelect(discord.ui.UserSelect):
    def __init__(self, action: str):
        super().__init__(placeholder="Select a member...", min_values=1, max_values=1, custom_id="sr_member_select")
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        action = self.action
        if action in ("ban", "mute"):
            modal = SRDurationReasonModal(action=action, target=member)
        else:
            modal = SRReasonOnlyModal(action=action, target=member)
        await interaction.response.send_modal(modal)

class SRMemberSelectView(discord.ui.View):
    def __init__(self, action: str, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.add_item(SRMemberSelect(action))

# (Include SRDurationReasonModal and SRReasonOnlyModal classes you already have)

@bot.tree.command(name="submit-report", description="Submit a moderation report (mods+ only)")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def submit_report(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message("You must be a moderator or admin to use this command.", ephemeral=True)
        return

    view = SRActionSelectView()
    await interaction.response.send_message("Choose an action for this report:", view=view, ephemeral=True)

# ============================================================
#                       on_message (combined handler)
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    # Ensure commands still processed
    await bot.process_commands(message)

    if message.author.bot:
        return

    # -------- PING-TO-DELETE FEATURE ----------
    if (
        message.guild is not None
        and message.guild.id == MAIN_GUILD_ID
        and bot.user is not None
        and bot.user in message.mentions
        and message.reference is not None
        and isinstance(message.reference.resolved, discord.Message)
    ):
        target_msg: discord.Message = message.reference.resolved
        try:
            await target_msg.delete()
        except Exception:
            pass
        # continue processing (do not return) so we still enforce other checks on the reply

    # -------- AUTO-MOD BAD_WORDS (main server text channels only) ----------
    if message.guild is not None and message.guild.id == MAIN_GUILD_ID and isinstance(message.channel, discord.TextChannel):
        lower = (message.content or "").lower()
        if any(bad in lower for bad in BAD_WORDS):
            try:
                await message.delete()
            except Exception:
                return
            log_ch = get_delete_log_channel()
            if log_ch is not None:
                channel_name = f"#{message.channel.name}"
                deleted_at = format_time(now_utc())
                content = message.content or "[no text]"
                embed = discord.Embed(
                    description=f"Auto-deleted bad message in {channel_name}",
                    color=discord.Color.dark_red()
                )
                embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
                embed.add_field(name="Content", value=content[:1024], inline=False)
                embed.set_footer(text=f"Deleted at {deleted_at}")
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass
            return  # stop further handling for this message

    # -------- SUSPICIOUS PROMO/SCAM DETECTION ----------
    try:
        suspicious = False

        if message_contains_suspicious_text(message.content):
            suspicious = True

        if not suspicious:
            for e in message.embeds:
                if embeds_contain_suspicious(e):
                    suspicious = True
                    break

        has_image = attachments_or_embeds_have_images(message)
        if not suspicious and has_image:
            if delete_images_always:
                suspicious = True
            else:
                if message_contains_suspicious_text(message.content):
                    suspicious = True
                else:
                    for e in message.embeds:
                        if embeds_contain_suspicious(e):
                            suspicious = True
                            break
                    if not suspicious and message_has_suspicious_link(message):
                        suspicious = True

        if not suspicious and message_has_suspicious_link(message):
            suspicious = True

        if suspicious:
            try:
                await message.delete()
            except Exception:
                pass

            log_ch = get_delete_log_channel()
            if log_ch is not None:
                channel_name = f"#{message.channel.name}" if isinstance(message.channel, discord.TextChannel) else "DM"
                embed = discord.Embed(
                    title="Auto-deleted suspicious message",
                    description=f"Deleted in {channel_name}",
                    color=discord.Color.dark_red()
                )
                embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
                embed.add_field(name="Content", value=(message.content or "[no text]")[:1024], inline=False)
                embed.add_field(name="Message ID", value=str(message.id), inline=False)
                if message.attachments:
                    urls = "\n".join(att.url for att in message.attachments)
                    embed.add_field(name="Attachments", value=urls[:1024], inline=False)
                embed.set_footer(text=format_time(now_utc()))
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass

            return
    except Exception:
        pass




class AgreementView(discord.ui.View):
    def __init__(self, user_id: int, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.user_id = user_id

    @discord.ui.button(label="I Agree", style=discord.ButtonStyle.success, custom_id="appeal_agree")
    async def agree_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your agreement.", ephemeral=True)
            return

        ok, msg = can_submit_appeal(interaction.user.id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        modal = AppealModal()
        await interaction.response.send_modal(modal)


class AppealModal(discord.ui.Modal, title="Ban Appeal Form"):
    date_reason = discord.ui.TextInput(
        label="1. DATE of ban and reason",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )
    explanation = discord.ui.TextInput(
        label="2. Explanation of incident",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    reason_for_appeal = discord.ui.TextInput(
        label="3. Reason for appeal / changes since ban",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    commitments = discord.ui.TextInput(
        label="4. Commitments to future behavior",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    comments = discord.ui.TextInput(
        label="5. Any additional comments",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        if interaction.guild is None or interaction.guild.id != APPEAL_GUILD_ID:
            await interaction.followup.send("This command can only be used in the appeal server.", ephemeral=True)
            return

        user = interaction.user
        history = appeal_history.setdefault(user.id, [])
        history.append(now_utc())

        if user.id not in pending_appeal_queue:
            pending_appeal_queue.append(user.id)
        position = pending_appeal_queue.index(user.id) + 1

        created_at = now_utc()

        embed = discord.Embed(
            title=f"{user} (@{user.name}) has submitted a ban appeal.",
            color=discord.Color.orange()
        )
        embed.add_field(name="User", value=f"{user.mention}", inline=False)
        embed.add_field(name="User ID", value=str(user.id), inline=False)
        embed.add_field(name="1. DATE of ban and reason", value=self.date_reason.value, inline=False)
        embed.add_field(name="2. Explanation of incident", value=self.explanation.value, inline=False)
        embed.add_field(name="3. Reason for appeal / changes since ban", value=self.reason_for_appeal.value, inline=False)
        embed.add_field(name="4. Commitments to future behavior", value=self.commitments.value, inline=False)
        embed.add_field(name="5. Any additional comments", value=self.comments.value if self.comments.value else "None", inline=False)
        embed.set_footer(text=format_time(created_at))

        channel = interaction.client.get_channel(APPEAL_CHANNEL_ID)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("Appeal channel not found or misconfigured.", ephemeral=True)
            return

        view = StaffDecisionView(target_user_id=user.id)
        staff_mention = f"<@&{STAFF_ROLE_ID}>" if STAFF_ROLE_ID else ""
        appeal_msg = await channel.send(content=staff_mention, embed=embed, view=view)

        thread = await appeal_msg.create_thread(name=f"Appeal - {user.name} ({user.id})", auto_archive_duration=1440)
        active_appeals[user.id] = thread.id

        try:
            dm_embed = discord.Embed(
                title="Appeal Started!",
                description="Your appeal has been started! Any messages you send here will be sent to the appeal team.\n\nFeel free to add any relevant information about your situation.",
                color=discord.Color.orange()
            )
            await user.send(embed=dm_embed)
        except Exception:
            pass

        await interaction.followup.send(f"Your appeal has been submitted to the appeal team.\nYou are currently **position {position}** in the appeal queue.", ephemeral=True)


@bot.tree.command(name="appeal", description="Submit a ban appeal")
@app_commands.guilds(discord.Object(id=APPEAL_GUILD_ID))
async def appeal(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != APPEAL_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the appeal server.", ephemeral=True)
        return

    ok, msg = can_submit_appeal(interaction.user.id)
    if not ok:
        await interaction.response.send_message(msg, ephemeral=True)
        return

    agreement_text = (
        "**Ban Appeal Agreement**\n"
        "By submitting this ban appeal, you agree to the following terms:\n\n"
        "• Only one unban request every 3 months.\n"
        "• There is a maximum appeal of 6 – if you are not accepted by the 6th appeal, you cannot appeal anymore.\n"
        "• Honesty is required. Dishonesty = immediate voiding of the appeal.\n"
        "• Submitting an appeal does not guarantee an unban.\n\n"
        "Click the button below to proceed."
    )

    view = AgreementView(user_id=interaction.user.id)
    await interaction.response.send_message(agreement_text, view=view, ephemeral=True)


    
    # -------- Relay DM messages to appeal thread ----------
    if message.guild is not None:
        return

    user_id = message.author.id
    if user_id not in active_appeals:
        return

    thread_id = active_appeals[user_id]
    thread = bot.get_channel(thread_id)
    if not isinstance(thread, discord.Thread):
        return

    content = message.content or "[no text]"
    attachments = message.attachments

    text = f"**Message from {message.author} ({message.author.id}) in DM:**\n{content}"

    files = []
    for att in attachments:
        try:
            files.append(await att.to_file())
        except Exception:
            pass

    await thread.send(content=text, files=files)

# Note: place the rest of your unchanged command implementations (kick, unban, false-ban, temp_ban_watcher, lock/purge, on_message_delete, on_ready, etc.)
# into this file as they were; ensure there are NO top-level await calls outside async functions.

# ---------- on_message_delete ----------
@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    if message.guild is None or message.guild.id != MAIN_GUILD_ID:
        return
    log_ch = get_delete_log_channel()
    if log_ch is None:
        return
    channel_name = f"#{message.channel.name}" if isinstance(message.channel, discord.TextChannel) else "Unknown channel"
    content = message.content or "[no text]"
    created_at_text = format_time(now_utc())
    embed = discord.Embed(
        description=f"Message deleted in {channel_name}",
        color=discord.Color.red()
    )
    embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
    embed.add_field(name="Content", value=content[:1024], inline=False)
    embed.add_field(name="Message ID", value=str(message.id), inline=False)
    embed.set_footer(text=f"Deleted at {created_at_text}")
    if message.attachments:
        urls = "\n".join(att.url for att in message.attachments)
        embed.add_field(name="Attachments", value=urls[:1024], inline=False)
    try:
        await log_ch.send(embed=embed)
    except Exception:
        pass


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    # Only care about guild messages in the main server
    if after.author.bot:
        return
    if after.guild is None or after.guild.id != MAIN_GUILD_ID:
        return

    # Check bad words (case-insensitive)
    content = (after.content or "").lower()
    if any(bad in content for bad in BAD_WORDS):
        try:
            await after.delete()
        except Exception:
            return

        log_ch = get_delete_log_channel()
        if log_ch is not None:
            channel_name = f"#{after.channel.name}" if isinstance(after.channel, discord.TextChannel) else "Unknown"
            embed = discord.Embed(
                title="Auto-deleted edited message (bad word)",
                description=f"Deleted in {channel_name}",
                color=discord.Color.dark_red()
            )
            embed.add_field(name="Author", value=f"{after.author} ({after.author.id})", inline=False)
            embed.add_field(name="Content (after edit)", value=(after.content or "[no text]")[:1024], inline=False)
            embed.add_field(name="Message ID", value=str(after.id), inline=False)
            embed.set_footer(text=format_time(now_utc()))
            try:
                await log_ch.send(embed=embed)
            except Exception:
                pass
        return

    # Also run the suspicious/promotional detection on edits
    try:
        suspicious = False
        if message_contains_suspicious_text(after.content):
            suspicious = True
        if not suspicious:
            for e in after.embeds:
                if embeds_contain_suspicious(e):
                    suspicious = True
                    break
        if not suspicious and attachments_or_embeds_have_images(after):
            if delete_images_always:
                suspicious = True
            else:
                if message_contains_suspicious_text(after.content) or message_has_suspicious_link(after):
                    suspicious = True
        if not suspicious and message_has_suspicious_link(after):
            suspicious = True

        if suspicious:
            try:
                await after.delete()
            except Exception:
                return

            log_ch = get_delete_log_channel()
            if log_ch is not None:
                channel_name = f"#{after.channel.name}" if isinstance(after.channel, discord.TextChannel) else "Unknown"
                embed = discord.Embed(
                    title="Auto-deleted edited message (suspicious)",
                    description=f"Deleted in {channel_name}",
                    color=discord.Color.dark_red()
                )
                embed.add_field(name="Author", value=f"{after.author} ({after.author.id})", inline=False)
                embed.add_field(name="Content (after edit)", value=(after.content or "[no text]")[:1024], inline=False)
                embed.add_field(name="Message ID", value=str(after.id), inline=False)
                if after.attachments:
                    urls = "\n".join(att.url for att in after.attachments)
                    embed.add_field(name="Attachments", value=urls[:1024], inline=False)
                embed.set_footer(text=format_time(now_utc()))
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass
            return
    except Exception:
        pass



# ---------- on_ready (ensure only one on_ready exists) ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    main_guild = discord.Object(id=MAIN_GUILD_ID)
    bot.tree.copy_global_to(guild=main_guild)
    await bot.tree.sync(guild=main_guild)
    appeal_guild = discord.Object(id=APPEAL_GUILD_ID)
    bot.tree.copy_global_to(guild=appeal_guild)
    await bot.tree.sync(guild=appeal_guild)
    try:
        if not temp_ban_watcher.is_running():
            temp_ban_watcher.start()
    except Exception:
        pass
    print("Slash commands synced for main and appeal guilds.")

# ---------- Start bot ----------
bot.run(os.getenv("TOKEN"))
