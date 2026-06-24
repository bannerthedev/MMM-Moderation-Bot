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
    "jack ass", "jackass", "motherfucker", "nigga", "pussy", "sisterfuck",
    "niggers","penis", "cocksucker", "retartd", "retarted", "rtrd", "nga", 
    "stfu","b1tch", "a$$", "jew",
]




# ---- Automod master switch (runtime toggle) ----
AUTOMOD_ENABLED = False  # False = OFF, True = ON

# Only these users can run /automod (PUT YOUR IDs HERE)
AUTOMOD_ALLOWED_USER_IDS = {
    1101643714033623120,  # <-- your user id
    1377076510896291941,
    1330370005732294669,
    919000592192536666,
}





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

# ---------- Appeal helper ----------
APPEAL_COOLDOWN = timedelta(days=90)
MAX_APPEALS = 6

def can_submit_appeal(user_id: int) -> Tuple[bool, str]:
    hist = appeal_history.get(user_id, [])
    if len(hist) >= MAX_APPEALS:
        return False, "You have reached the maximum number of appeals and cannot appeal anymore."
    last = hist[-1] if hist else None
    if last and (now_utc() - last) < APPEAL_COOLDOWN:
        remaining = APPEAL_COOLDOWN - (now_utc() - last)
        hours = int(remaining.total_seconds() // 3600)
        return False, f"You must wait {hours} hour(s) before submitting another appeal."
    return True, "OK"

def get_log_channel() -> Optional[discord.TextChannel]:
    ch = bot.get_channel(LOG_CHANNEL_ID)
    return ch if isinstance(ch, discord.TextChannel) else None

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

# Auto-mod escalation state
bad_word_offenses: Dict[int, int] = {}
last_offense_time: Dict[int, datetime] = {}
MAX_TIMEOUT_DAYS = 30
BAN_ON_REOFFEND_WITHIN_DAYS = 7
BAN_DURATION_DAYS = 60

# ============================================================
#                       /submit-report
# ============================================================

class SRDurationReasonModal(discord.ui.Modal, title="Action (duration + reason)"):
    duration = discord.ui.TextInput(
        label="Duration (e.g. 7d, 12h, 30m, perm)",
        required=True,
        max_length=20,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, action: str, target: discord.User):
        super().__init__()
        self.action = action  # "ban" or "mute"
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        # Basic checks
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message(
                "This command can only be used in the main server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
            await interaction.response.send_message(
                "You must be a moderator or admin to use this command.",
                ephemeral=True,
            )
            return

        raw_duration = self.duration.value.strip()
        td = parse_duration(raw_duration)  # None => permanent
        reason = self.reason.value.strip() or "No reason provided."

        # ---------------- BAN ----------------
        if self.action == "ban":
            guild = interaction.guild
            try:
                await guild.ban(
                    discord.Object(id=self.target.id),
                    reason=reason,
                    delete_message_seconds=0,
                )
            except Exception as e:
                await interaction.response.send_message(
                    f"Failed to ban user: `{e}`",
                    ephemeral=True,
                )
                return

            # DM user (best effort)
            try:
                dm_embed = discord.Embed(
                    title=f"You have been banned from {SERVER_NAME}",
                    description=(
                        f"**Reason:** {reason}\n\n"
                        f"If you believe this was in error, you may appeal here:\n{APPEAL_LINK}"
                    ),
                    color=discord.Color.dark_red(),
                )
                await self.target.send(embed=dm_embed)
            except Exception:
                pass

            # Log to mod log channel
            log_ch = get_log_channel()
            if log_ch is not None:
                case_id = get_next_case_id()
                now = now_utc()
                offender_str = f"{self.target.id} {getattr(self.target, 'mention', '')}"
                dur_text = "Permanent" if td is None else raw_duration
                embed = discord.Embed(
                    title=f"ban | case {case_id}",
                    color=discord.Color.dark_red(),
                )
                embed.add_field(name="Offender:", value=offender_str, inline=False)
                embed.add_field(name="Reason:", value=reason, inline=False)
                embed.add_field(name="Duration:", value=dur_text, inline=False)
                embed.add_field(
                    name="ID / Time:",
                    value=f"{self.target.id} • {format_time(now)}",
                    inline=False,
                )
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass

            await interaction.response.send_message(
                f"✅ Banned {self.target.mention} (`{self.target.id}`)\n**Reason:** {reason}",
                ephemeral=True,
            )
            return

        # ---------------- MUTE / TIMEOUT ----------------
        elif self.action == "mute":
            if td is None:
                await interaction.response.send_message(
                    "Mute must have a finite duration (no permanent mutes via this form).",
                    ephemeral=True,
                )
                return

            guild = interaction.guild
            member = guild.get_member(self.target.id)
            if member is None:
                await interaction.response.send_message(
                    "That user is not in the server.",
                    ephemeral=True,
                )
                return

            end_time = now_utc() + td
            try:
                await member.edit(timeout=end_time, reason=reason)
            except Exception as e:
                await interaction.response.send_message(
                    f"Failed to timeout user: `{e}`",
                    ephemeral=True,
                )
                return

            # DM user (best effort)
            try:
                dm_embed = discord.Embed(
                    title=f"You have been muted in {SERVER_NAME}",
                    description=(
                        f"**Duration:** {raw_duration}\n"
                        f"**Reason:** {reason}"
                    ),
                    color=discord.Color.orange(),
                )
                await member.send(embed=dm_embed)
            except Exception:
                pass

            # Log to mod log channel
            log_ch = get_log_channel()
            if log_ch is not None:
                case_id = get_next_case_id()
                now = now_utc()
                offender_str = f"{member.id} {member.mention}"
                embed = discord.Embed(
                    title=f"mute | case {case_id}",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Offender:", value=offender_str, inline=False)
                embed.add_field(name="Reason:", value=reason, inline=False)
                embed.add_field(name="Duration:", value=raw_duration, inline=False)
                embed.add_field(
                    name="ID / Time:",
                    value=f"{member.id} • {format_time(now)}",
                    inline=False,
                )
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass

            await interaction.response.send_message(
                f"✅ Muted {member.mention} (`{member.id}`) for `{raw_duration}`\n**Reason:** {reason}",
                ephemeral=True,
            )
            return

        # Fallback
        await interaction.response.send_message(
            "Unknown action.",
            ephemeral=True,
        )


class SRReasonOnlyModal(discord.ui.Modal, title="Action (reason only)"):
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, action: str, target: discord.User):
        super().__init__()
        self.action = action  # "warning"
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message(
                "This command can only be used in the main server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
            await interaction.response.send_message(
                "You must be a moderator or admin to use this command.",
                ephemeral=True,
            )
            return

        reason = self.reason.value.strip() or "No reason provided."

        # Only "warning" currently uses this modal
        if self.action != "warning":
            await interaction.response.send_message(
                "Unknown action.",
                ephemeral=True,
            )
            return

        # DM user warning (best effort)
        try:
            dm_embed = discord.Embed(
                title=f"You have received a warning in {SERVER_NAME}",
                description=f"**Reason:** {reason}",
                color=discord.Color.yellow(),
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log warning
        log_ch = get_log_channel()
        if log_ch is not None:
            case_id = get_next_case_id()
            now = now_utc()
            offender_str = f"{self.target.id} {getattr(self.target, 'mention', '')}"
            embed = discord.Embed(
                title=f"warning | case {case_id}",
                color=discord.Color.yellow(),
            )
            embed.add_field(name="Offender:", value=offender_str, inline=False)
            embed.add_field(name="Reason:", value=reason, inline=False)
            embed.add_field(
                name="ID / Time:",
                value=f"{self.target.id} • {format_time(now)}",
                inline=False,
            )
            try:
                await log_ch.send(embed=embed)
            except Exception:
                pass

        await interaction.response.send_message(
            f"✅ Warning recorded for {self.target.mention} (`{self.target.id}`)\n**Reason:** {reason}",
            ephemeral=True,
        )


class SRMemberSelect(discord.ui.UserSelect):
    def __init__(self, action: str):
        super().__init__(
            placeholder="Select a member...",
            min_values=1,
            max_values=1,
            custom_id="sr_member_select",
        )
        self.action = action  # "ban" / "mute" / "warning"

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


class SRActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Ban", value="ban"),
            discord.SelectOption(label="Warning", value="warning"),
            discord.SelectOption(label="Mute/Timeout", value="mute"),
        ]
        super().__init__(
            placeholder="Choose an action...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="sr_action_select",
        )

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        view = SRMemberSelectView(action)
        await interaction.response.edit_message(
            content=f"Action selected: **{action.capitalize()}**. Now choose a member:",
            view=view,
        )


class SRActionSelectView(discord.ui.View):
    def __init__(self, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.add_item(SRActionSelect())


@bot.tree.command(name="submit-report", description="Submit a moderation report (mods+ only)")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def submit_report(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message(
            "This command can only be used in the main server.",
            ephemeral=True,
        )
        return

    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message(
            "You must be a moderator or admin to use this command.",
            ephemeral=True,
        )
        return

    view = SRActionSelectView()
    await interaction.response.send_message(
        "Choose an action for this report:",
        view=view,
        ephemeral=True,
    )


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

    # -------- AUTO-MOD BAD_WORDS (escalating timeouts -> ban) ----------
    if AUTOMOD_ENABLED and message.guild is not None and message.guild.id == MAIN_GUILD_ID and isinstance(message.channel, discord.TextChannel):
        content_lower = (message.content or "").lower()
        if any(bad in content_lower for bad in BAD_WORDS):
            uid = message.author.id
            now = now_utc()

            # check recent offense -> escalate to temporary ban-after-timeout condition
            last_time = last_offense_time.get(uid)
            if last_time is not None and (now - last_time) <= timedelta(days=BAN_ON_REOFFEND_WITHIN_DAYS):
                # Instead of immediate permanent ban, apply a strict timeout of BAN_DURATION_DAYS
                end_time = now + timedelta(days=BAN_DURATION_DAYS)
                # Try to timeout member first
                if isinstance(message.author, discord.Member):
                    try:
                        await message.author.edit(timeout=end_time)
                    except Exception:
                        pass
                # Mark as temp ban to be enforced (bot will ban when timeout expires)
                temp_bans[uid] = end_time

                # notify user via DM that they will be banned after the timeout ends
                try:
                    await message.author.send(
                        f"You have been timed out in {SERVER_NAME} and will be banned after the timeout expires due to repeated rule violations."
                    )
                except Exception:
                    pass

                # Log scheduled ban
                log_ch = get_log_channel()
                if log_ch is not None:
                    embed = discord.Embed(title=f"Auto-schedule ban (case)", color=discord.Color.dark_red())
                    embed.add_field(name="Offender:", value=f"{uid} {getattr(message.author, 'mention', '')}", inline=False)
                    embed.add_field(name="Reason:", value=f"Repeated bad-language offenses. Timed out and scheduled ban for {BAN_DURATION_DAYS} days.", inline=False)
                    embed.add_field(name="Message", value=(message.content or "[no text]")[:1024], inline=False)
                    embed.set_footer(text=format_time(now))
                    try:
                        await log_ch.send(embed=embed)
                    except Exception:
                        pass

                # clear offense counters
                bad_word_offenses.pop(uid, None)
                last_offense_time.pop(uid, None)

                # delete offending message if still present
                try:
                    await message.delete()
                except Exception:
                    pass

                return

            # Otherwise apply escalating timeout
            count = bad_word_offenses.get(uid, 0) + 1
            bad_word_offenses[uid] = count
            last_offense_time[uid] = now

            days = min(count, MAX_TIMEOUT_DAYS)
            end_time = now + timedelta(days=days)

            # attempt to timeout the member
            if isinstance(message.author, discord.Member):
                try:
                    await message.author.edit(timeout=end_time)
                except Exception:
                    pass

            # DM user about timeout
            try:
                embed = discord.Embed(
                    title=f"You Have Been Muted In {SERVER_NAME}",
                    description=(
                        f"You used disallowed language. This is offense #{count}.\n"
                        f"You have been timed out for {days} day{'s' if days != 1 else ''}.\n\n"
                        "Please follow the server rules:\n"
                        f"• {SERVER_RULES_LINK}\n"
                        f"• {GAME_RULEBOOK_LINK}"
                    ),
                    color=discord.Color.orange()
                )
                embed.set_footer(text=format_time(now))
                await message.author.send(embed=embed)
            except Exception:
                pass

            # delete offending message
            try:
                await message.delete()
            except Exception:
                pass

            # Log timeout
            log_ch = get_delete_log_channel()
            if log_ch is not None:
                channel_name = f"#{message.channel.name}"
                embed = discord.Embed(
                    title="Auto-timeout (bad word)",
                    description=f"Deleted and timed out in {channel_name}",
                    color=discord.Color.dark_orange()
                )
                embed.add_field(name="Author", value=f"{message.author} ({uid})", inline=False)
                embed.add_field(name="Offense #", value=str(count), inline=True)
                embed.add_field(name="Duration", value=f"{days} day{'s' if days != 1 else ''}", inline=True)
                embed.add_field(name="Message", value=(message.content or "[no text]")[:1024], inline=False)
                embed.set_footer(text=format_time(now))
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass

            return

    # -------- SUSPICIOUS PROMO/SCAM DETECTION ----------
    if AUTOMOD_ENABLED:
        try:
            suspicious = False

            # 1) content keywords
            if message_contains_suspicious_text(message.content):
                suspicious = True

            # 2) embed text keywords
            if not suspicious:
                for e in message.embeds:
                    if embeds_contain_suspicious(e):
                        suspicious = True
                        break

            # 3) image + optional extra checks
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

            # 4) suspicious domains/links
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

    # -------- Relay DM messages to appeal thread ----------
    if message.guild is None:
        user_id = message.author.id
        if user_id not in active_appeals:
            return

        thread_id = active_appeals.get(user_id)
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

        try:
            await thread.send(content=text, files=files)
        except Exception:
            pass


# create the group (no guild arg)
purge = app_commands.Group(
    name="purge",
    description="Purge messages in a channel"
)

@purge.command(name="all", description="Delete a number of recent messages in this channel.")
@app_commands.describe(count="How many recent messages to delete (max 1000 recommended)")
async def purge_all(interaction: discord.Interaction, count: int):
    # Ensure this runs only in the main guild
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
        return

    # Admins only
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
        return

    if count <= 0:
        await interaction.response.send_message("Please provide a positive number of messages to delete.", ephemeral=True)
        return

    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("This command can only be used in text channels or threads.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    messages = []
    async for msg in channel.history(limit=count):
        messages.append(msg)

    if not messages:
        await interaction.followup.send("No messages found to delete.", ephemeral=True)
        return

    per_user: Dict[str, int] = {}
    for msg in messages:
        name = f"{msg.author} ({msg.author.id})"
        per_user[name] = per_user.get(name, 0) + 1

    try:
        await channel.delete_messages(messages)
    except Exception as e:
        await interaction.followup.send(f"Failed to delete messages: `{e}`", ephemeral=True)
        return

    total_deleted = len(messages)
    lines = [f"{total_deleted} messages were removed.", ""]
    for user_name, amt in sorted(per_user.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"{user_name} – {amt}")
    summary = "\n".join(lines)

    await channel.send(summary)
    await interaction.followup.send("Purge complete.", ephemeral=True)

bot.tree.add_command(purge)


@bot.tree.command(name="lock-down", description="Lock this channel so only mods+ can talk.")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def lock_down(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True); return
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message("You must be a moderator or admin to use this command.", ephemeral=True); return
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("This command can only be used in text channels or threads.", ephemeral=True); return
    try:
        await channel.set_permissions(interaction.guild.default_role, send_messages=False)
    except Exception as e:
        await interaction.response.send_message(f"Failed to lock this channel: `{e}`", ephemeral=True); return
    await interaction.response.send_message("This channel has been **locked**. Only staff can talk now.", ephemeral=True)

@bot.command(name="lock")
@commands.guild_only()
async def lock_prefix(ctx: commands.Context):
    if ctx.guild is None or ctx.guild.id != MAIN_GUILD_ID:
        return
    if not isinstance(ctx.author, discord.Member) or not is_mod_or_admin(ctx.author):
        await ctx.reply("You must be a moderator or admin to use this command.", mention_author=False); return
    channel = ctx.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await ctx.reply("This command can only be used in text channels or threads.", mention_author=False); return
    try:
        await channel.set_permissions(ctx.guild.default_role, send_messages=False)
    except Exception as e:
        await ctx.reply(f"Failed to lock this channel: `{e}`", mention_author=False); return
    await ctx.reply("This channel has been **locked**. Only staff can talk now.", mention_author=False)


@bot.tree.command(name="unban", description="Unban a user from the main server")
@app_commands.describe(user_id="ID of the user to unban (right click -> Copy ID)", reason="Reason for the unban (optional)")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "Manual unban"):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True); return
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message("You must be a moderator or admin to use this command.", ephemeral=True); return
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message("Please provide a valid user ID.", ephemeral=True); return
    if uid in permanent_bans:
        await interaction.response.send_message("This user has a **permanent ban** and cannot be unbanned via this command.", ephemeral=True); return

    user = None
    try:
        user = await interaction.client.fetch_user(uid)
    except Exception:
        user = interaction.client.get_user(uid)

    try:
        await interaction.guild.unban(discord.Object(id=uid), reason=reason)
    except discord.NotFound:
        await interaction.response.send_message("That user is not currently banned.", ephemeral=True); return
    except Exception as e:
        await interaction.response.send_message(f"Failed to unban user: `{e}`", ephemeral=True); return

    permanent_bans.discard(uid)
    earliest_appeal_time.pop(uid, None)
    temp_bans.pop(uid, None)

    appeal_guild = interaction.client.get_guild(APPEAL_GUILD_ID)
    if appeal_guild is not None:
        try:
            member = appeal_guild.get_member(uid)
            if member:
                await member.kick(reason="Unbanned from main server - removed from appeal server")
        except Exception:
            pass

    log_ch = get_log_channel()
    if log_ch is not None and interaction.guild.id == MAIN_GUILD_ID:
        case_id = get_next_case_id()
        now = now_utc()
        reason_text = reason if reason and reason.strip() not in ("Manual unban",) else f"No reason given, use !reason {case_id} <text> to add one"
        offender_user = user or interaction.client.get_user(uid)
        offender_str = f"{uid} {offender_user.mention}" if offender_user else str(uid)
        log_embed = discord.Embed(title=f"unban | case {case_id}", color=discord.Color.green())
        log_embed.add_field(name="Offender:", value=offender_str, inline=False)
        log_embed.add_field(name="Reason:", value=reason_text, inline=False)
        log_embed.add_field(name="ID:", value=f"{uid} • {format_time(now)}", inline=False)
        try:
            await log_ch.send(embed=log_embed)
        except Exception:
            pass

    if user is not None:
        try:
            embed = discord.Embed(title="You Have Been Unbanned", description=f"[our main server]({MAIN_SERVER_INVITE})", color=discord.Color.green())
            await user.send(embed=embed)
        except Exception:
            pass

    await interaction.response.send_message(f"User with ID `{uid}` has been **unbanned**.\nReason: {reason}", ephemeral=True)






@bot.tree.command(name="automod", description="Toggle automod on/off (authorized users only)")
@app_commands.describe(state="Turn automod on or off")
@app_commands.choices(state=[
    app_commands.Choice(name="on", value="on"),
    app_commands.Choice(name="off", value="off"),
])
async def automod(interaction: discord.Interaction, state: app_commands.Choice[str]):
    if interaction.user.id not in AUTOMOD_ALLOWED_USER_IDS:
        await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)
        return

    global AUTOMOD_ENABLED
    AUTOMOD_ENABLED = (state.value == "on")

    # Start/stop watcher accordingly (only relevant if you use the ban-after-timeout logic)
    try:
        if AUTOMOD_ENABLED:
            if not temp_ban_watcher.is_running():
                temp_ban_watcher.start()
        else:
            if temp_ban_watcher.is_running():
                temp_ban_watcher.stop()
    except Exception:
        pass

    await interaction.response.send_message(
        f"Automod is now **{'ON' if AUTOMOD_ENABLED else 'OFF'}**.",
        ephemeral=True
    )





@bot.tree.command(name="false-ban", description="Unban a user due to a false ban and notify them.")
@app_commands.describe(user_id="ID of the user to unban (right click -> Copy ID)")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def false_ban(interaction: discord.Interaction, user_id: str):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True); return
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message("You must be a moderator or admin to use this command.", ephemeral=True); return
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message("Please provide a valid user ID.", ephemeral=True); return
    if uid in permanent_bans:
        await interaction.response.send_message("This user has a **permanent ban** and cannot be unbanned via this command.", ephemeral=True); return

    user = None
    try:
        user = await interaction.client.fetch_user(uid)
    except Exception:
        user = interaction.client.get_user(uid)

    try:
        await interaction.guild.unban(discord.Object(id=uid), reason="False ban correction")
    except discord.NotFound:
        await interaction.response.send_message("That user is not currently banned.", ephemeral=True); return
    except Exception as e:
        await interaction.response.send_message(f"Failed to unban user: `{e}`", ephemeral=True); return

    permanent_bans.discard(uid)
    earliest_appeal_time.pop(uid, None)
    temp_bans.pop(uid, None)

    appeal_guild = interaction.client.get_guild(APPEAL_GUILD_ID)
    if appeal_guild is not None:
        try:
            member = appeal_guild.get_member(uid)
            if member:
                await member.kick(reason="False ban corrected - removed from appeal server")
        except Exception:
            pass

    log_ch = get_log_channel()
    if log_ch is not None and interaction.guild.id == MAIN_GUILD_ID:
        now = now_utc()
        offender_user = user or interaction.client.get_user(uid)
        offender_str = f"{uid} {offender_user.mention}" if offender_user else str(uid)
        log_embed = discord.Embed(title="False ban", color=discord.Color.magenta())
        log_embed.add_field(name="Offender:", value=offender_str, inline=False)
        log_embed.add_field(name="Reason:", value="False ban – staff corrected the ban.", inline=False)
        log_embed.set_footer(text=format_time(now))
        try:
            await log_ch.send(embed=log_embed)
        except Exception:
            pass

    if user is not None:
        try:
            msg = ("A False Ban Was Issued! We are very sorry for the inconvenience,\n"
                   f"{MAIN_SERVER_INVITE}\nBest regards, MMM Staff Team.")
            await user.send(msg)
        except Exception:
            pass

    await interaction.response.send_message(f"User with ID `{uid}` has been **unbanned** due to a false ban.", ephemeral=True)


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


class StaffDecisionView(discord.ui.View):
    # Placeholder so your AppealModal can instantiate it; implement your real buttons here
    def __init__(self, target_user_id: int, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.target_user_id = target_user_id


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

        await interaction.followup.send(
            f"Your appeal has been submitted to the appeal team.\nYou are currently **position {position}** in the appeal queue.",
            ephemeral=True
        )


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
    if after.author.bot:
        return
    if after.guild is None or after.guild.id != MAIN_GUILD_ID:
        return

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


@tasks.loop(seconds=60)
async def temp_ban_watcher():
    now = now_utc()
    to_ban: List[int] = []
    for uid, end_time in list(temp_bans.items()):
        if now >= end_time:
            to_ban.append(uid)

    if not to_ban:
        return

    guild = bot.get_guild(MAIN_GUILD_ID)
    if guild is None:
        return

    for uid in to_ban:
        temp_bans.pop(uid, None)
        try:
            try:
                user = await bot.fetch_user(uid)
            except Exception:
                user = bot.get_user(uid)

            await guild.ban(
                discord.Object(id=uid),
                reason="Auto-ban after timeout expired (repeated offenses)",
                delete_message_seconds=0,
            )

            if user is not None:
                try:
                    await user.send(f"You have been banned from {SERVER_NAME} due to repeated rule violations.")
                except Exception:
                    pass

            log_ch = get_log_channel()
            if log_ch is not None:
                offender_str = f"{uid} {user.mention}" if user else str(uid)
                embed = discord.Embed(title="Auto-ban enacted", color=discord.Color.dark_red())
                embed.add_field(name="Offender:", value=offender_str, inline=False)
                embed.add_field(
                    name="Reason:",
                    value="Auto-ban after timeout expired (repeated bad-language offenses)",
                    inline=False,
                )
                embed.set_footer(text=format_time(now_utc()))
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass
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
        if AUTOMOD_ENABLED and (not temp_ban_watcher.is_running()):
            temp_ban_watcher.start()
    except Exception:
        pass

    print("Slash commands synced for main and appeal guilds.")


# ---------- Start bot ----------
bot.run(os.getenv("TOKEN"))
