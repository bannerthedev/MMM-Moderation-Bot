import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional
import os
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ------------ IDs / CONSTANTS ------------
MAIN_GUILD_ID = 1338455645896310784   # main server ID
APPEAL_GUILD_ID = 1497651620681486338 # appeal server ID
APPEAL_CHANNEL_ID = 1497668613283254312  # appeal review channel ID (in appeal server)
STAFF_ROLE_ID = 1497662209285689575     # staff role in appeal server (ping + permissions)

# ROLES IN MAIN SERVER
MOD_ROLE_ID = 1339202997208616990       # <<< REPLACE with your Mod role ID
TRIAL_MOD_ROLE_ID = 1374305296326856734 # <<< REPLACE with your Trial Mod role ID

MAIN_SERVER_INVITE = "https://discord.gg/mmml"
SERVER_NAME = "Monke Monke Monke League"
APPEAL_LINK = "https://discord.gg/Dn9N2GdGVT"  # for ban DM

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.dm_messages = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Shared helpers ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


EST = ZoneInfo("America/New_York")

def format_time(dt):
    dt = dt.astimezone(EST)
    return dt.strftime("%m/%d/%Y %I:%M %p EST")

def parse_duration(text: str) -> Optional[timedelta]:
    if not text:
        return None
    text = text.strip().lower()

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

    return None

def format_remaining(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "Expired"

    hours, rem = divmod(total_seconds, 3600)
    minutes, _ = divmod(rem, 60)
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
                    if field.name == "Duration":
                        embed.set_field_at(i, name="Duration", value="Expired", inline=False)
                        break
                await message.edit(embed=embed)
            except Exception:
                pass
            break

        remaining_text = format_remaining(remaining)
        try:
            embed = message.embeds[0]
            for i, field in enumerate(embed.fields):
                if field.name == "Duration":
                    embed.set_field_at(i, name="Duration", value=remaining_text, inline=False)
                    break
            await message.edit(embed=embed)
        except Exception:
            break

        await asyncio.sleep(60)


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


# ============================================================
#                       /submit-report
# ============================================================

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
            custom_id="sr_action_select"
        )

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        view = SRMemberSelectView(action)
        await interaction.response.edit_message(
            content=f"Action selected: **{action.capitalize()}**. Now choose a member:",
            view=view
        )

class SRActionSelectView(discord.ui.View):
    def __init__(self, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.add_item(SRActionSelect())

class SRMemberSelect(discord.ui.UserSelect):
    def __init__(self, action: str):
        super().__init__(
            placeholder="Select a member...",
            min_values=1,
            max_values=1,
            custom_id="sr_member_select"
        )
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

class SRDurationReasonModal(discord.ui.Modal, title="Submit Report"):
    def __init__(self, action: str, target: discord.Member | discord.User):
        super().__init__(timeout=300)
        self.action = action
        self.target = target

        self.duration_input = discord.ui.TextInput(
            label="Duration (e.g. 2h, 5 days, perm)",
            placeholder="2h or 3 month ban or perm ban",
            required=True,
            max_length=50
        )
        self.reason_input = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Because...",
            required=True,
            max_length=400
        )
        self.add_item(self.duration_input)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Defer to keep interaction alive
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.followup.send(
                "This command can only be used in the main server.",
                ephemeral=True
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "You must be in the server to use this.",
                ephemeral=True
            )
            return

        if self.action == "ban" and not is_mod_or_admin(interaction.user):
            await interaction.followup.send(
                "You must be a moderator or admin to ban members.",
                ephemeral=True
            )
            return

        if self.action == "mute" and not can_timeout(interaction.user):
            await interaction.followup.send(
                "You must be staff (Trial Mod or higher) to mute members.",
                ephemeral=True
            )
            return

        duration_text = self.duration_input.value
        reason = self.reason_input.value
        now = now_utc()

        if self.action == "ban":
            duration_text = duration_text.strip()
            lower = duration_text.lower()

            is_perm = False
            is_three_month = False
            delta: Optional[timedelta] = None
            end_time: Optional[datetime] = None

            if any(word in lower for word in ("perm ban", "perma ban", "permanent ban", "permban", "permanent", "perm")):
                is_perm = True
                delta = None
                end_time = None
            elif any(phrase in lower for phrase in ("3 month ban", "3 months ban", "3 month", "3 months", "3mo")):
                is_three_month = True
                delta = timedelta(days=90)
                end_time = now + delta
            else:
                delta = parse_duration(duration_text)
                if delta is None:
                    await interaction.followup.send(
                        "Could not parse that duration. Use things like `2h`, `5 days`, `30m`, "
                        "or `perm ban` / `3 month ban`.",
                        ephemeral=True
                    )
                    return
                end_time = now + delta

            embed = discord.Embed(
                title="You have been banned.",
                description=f"Appeal: join [this server]({APPEAL_LINK}) and run `/appeal`.",
                color=discord.Color.red()
            )
            embed.add_field(name="Rule", value="Testing server", inline=False)

            if is_perm:
                duration_field_value = "Permanent ban – you cannot appeal this ban."
            elif is_three_month:
                duration_field_value = "3 months – you may appeal after 3 months."
            else:
                duration_field_value = format_remaining(delta)

            embed.add_field(name="Duration", value=duration_field_value, inline=False)
            embed.set_footer(text=format_time(now))

            dm_msg = None
            try:
                dm_msg = await self.target.send(embed=embed)
            except Exception:
                dm_msg = None

            user_id = self.target.id
            if is_perm:
                permanent_bans.add(user_id)
                earliest_appeal_time.pop(user_id, None)
            elif is_three_month:
                earliest_appeal_time[user_id] = end_time
            else:
                earliest_appeal_time.pop(user_id, None)

            guild = interaction.guild
            try:
                await guild.ban(self.target, reason=reason, delete_message_seconds=0)
            except Exception as e:
                await interaction.followup.send(
                    f"Failed to ban user: {e}",
                    ephemeral=True
                )
                return

            await interaction.followup.send(
                f"{self.target.mention} has been **banned** for `{duration_text}`.\nReason: {reason}",
                ephemeral=True
            )

            if dm_msg and end_time is not None:
                bot.loop.create_task(setup_countdown(dm_msg, end_time))

        elif self.action == "mute":
            delta = parse_duration(duration_text)
            if delta is None:
                await interaction.followup.send(
                    "Could not parse that duration. Use things like `2h`, `5 days`, `30m`.",
                    ephemeral=True
                )
                return
            end_time = now + delta

            embed = discord.Embed(
                title=f"You Have Been Muted In {SERVER_NAME}",
                color=discord.Color.dark_gray()
            )
            embed.add_field(name="Reason:", value=reason, inline=False)
            embed.add_field(name="Duration:", value=duration_text, inline=False)
            embed.set_footer(text=format_time(now))

            try:
                await self.target.send(embed=embed)
            except Exception:
                pass

            if isinstance(self.target, discord.Member):
                try:
                    await self.target.edit(timeout=end_time)
                except Exception:
                    pass

            await interaction.followup.send(
                f"{self.target.mention} has been **muted** for `{duration_text}`.\nReason: {reason}",
                ephemeral=True
            )

class SRReasonOnlyModal(discord.ui.Modal, title="Submit Report"):
    def __init__(self, action: str, target: discord.Member | discord.User):
        super().__init__(timeout=300)
        self.action = action
        self.target = target
        self.reason_input = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Because...",
            required=True,
            max_length=400
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Defer to keep interaction alive
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.followup.send(
                "This command can only be used in the main server.",
                ephemeral=True
            )
            return
        if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
            await interaction.followup.send(
                "You must be a moderator or admin to use this.",
                ephemeral=True
            )
            return

        reason = self.reason_input.value
        now = now_utc()

        if self.action == "warning":
            embed = discord.Embed(
                title=f"You have been warned in {SERVER_NAME}",
                color=discord.Color.yellow()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.set_footer(text=format_time(now))

            try:
                await self.target.send(embed=embed)
            except Exception:
                pass

            await interaction.followup.send(
                f"{self.target.mention} has been **warned**.\nReason: {reason}",
                ephemeral=True
            )


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
    await interaction.response.send_message(
        "Choose an action for this report:",
        view=view,
        ephemeral=True
    )


# ============================================================
#                           /appeal
# ============================================================

appeal_history: Dict[int, List[datetime]] = {}
active_appeals: Dict[int, int] = {}          # user_id -> thread_id
pending_appeal_queue: List[int] = []         # user ids in queue order

permanent_bans: set[int] = set()                     # users who can never appeal
earliest_appeal_time: Dict[int, datetime] = {}       # user_id -> earliest time they may appeal


def can_submit_appeal(user_id: int) -> Tuple[bool, Optional[str]]:
    if user_id in permanent_bans:
        return False, "You were permanently banned and cannot appeal."

    if user_id in earliest_appeal_time:
        now = now_utc()
        unlock = earliest_appeal_time[user_id]
        if now < unlock:
            return False, (
                "You may not appeal yet.\n"
                f"Earliest appeal time: **{format_time(unlock)}**"
            )

    history = appeal_history.get(user_id, [])
    if len(history) >= 6:
        return False, "You have reached the maximum of 6 appeals and cannot appeal anymore."

    if history:
        last = max(history)
        if now_utc() - last < timedelta(days=90):
            return False, "You may only submit one appeal every 3 months. Please try again later."

    return True, None

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
        # Defer to avoid timeout / unknown interaction
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        if interaction.guild is None or interaction.guild.id != APPEAL_GUILD_ID:
            await interaction.followup.send(
                "This command can only be used in the appeal server.",
                ephemeral=True
            )
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
        embed.add_field(
            name="5. Any additional comments",
            value=self.comments.value if self.comments.value else "None",
            inline=False
        )
        embed.set_footer(text=format_time(created_at))

        channel = interaction.client.get_channel(APPEAL_CHANNEL_ID)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send(
                "Appeal channel not found or misconfigured.",
                ephemeral=True
            )
            return

        view = StaffDecisionView(target_user_id=user.id)
        staff_mention = f"<@&{STAFF_ROLE_ID}>" if STAFF_ROLE_ID else ""
        appeal_msg = await channel.send(content=staff_mention, embed=embed, view=view)

        thread = await appeal_msg.create_thread(
            name=f"Appeal - {user.name} ({user.id})",
            auto_archive_duration=1440
        )
        active_appeals[user.id] = thread.id

        try:
            dm_embed = discord.Embed(
                title="Appeal Started!",
                description=(
                    "Your appeal has been started! Any messages you send here will be sent to the appeal team.\n\n"
                    "Feel free to add any relevant information about your situation."
                ),
                color=discord.Color.orange()
            )
            await user.send(embed=dm_embed)
        except Exception:
            pass

        await interaction.followup.send(
            f"Your appeal has been submitted to the appeal team.\n"
            f"You are currently **position {position}** in the appeal queue.",
            ephemeral=True
        )

class StaffDecisionView(discord.ui.View):
    def __init__(self, target_user_id: int, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.target_user_id = target_user_id

    async def _check_staff(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("Not in a guild.", ephemeral=True)
            return False
        if STAFF_ROLE_ID is None:
            return True
        role = interaction.guild.get_role(STAFF_ROLE_ID)
        if not isinstance(interaction.user, discord.Member) or role not in interaction.user.roles:
            await interaction.response.send_message("You do not have permission to handle appeals.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="appeal_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_staff(interaction):
            return

        user_id = self.target_user_id
        client = interaction.client

        try:
            user = await client.fetch_user(user_id)
        except Exception:
            user = client.get_user(user_id)

        if user is not None:
            try:
                msg = (
                    "You have Been Unbanned join our server here:\n"
                    "[Main Server](https://discord.gg/monkemonkemonke)"
                )
                await user.send(msg)
            except Exception:
                pass

        main_guild = client.get_guild(MAIN_GUILD_ID)
        if main_guild:
            try:
                await main_guild.unban(discord.Object(id=user_id), reason="Appeal accepted")
            except discord.NotFound:
                pass
            except Exception:
                pass

        appeal_guild = client.get_guild(APPEAL_GUILD_ID)
        if appeal_guild:
            try:
                member = appeal_guild.get_member(user_id)
                if member:
                    await member.kick(reason="Appeal accepted - removed from appeal server")
            except Exception:
                pass

        if user_id in pending_appeal_queue:
            pending_appeal_queue.remove(user_id)
        active_appeals.pop(user_id, None)

        permanent_bans.discard(user_id)
        earliest_appeal_time.pop(user_id, None)

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(content="Appeal **ACCEPTED**.", view=self)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="appeal_deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_staff(interaction):
            return

        user_id = self.target_user_id
        user = interaction.client.get_user(user_id)
        if user is not None:
            try:
                await user.send("Your appeal has been Denied")
            except Exception:
                pass

        if user_id in pending_appeal_queue:
            pending_appeal_queue.remove(user_id)
        active_appeals.pop(user_id, None)

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(content="Appeal **DENIED**.", view=self)


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


# ---------- Relay DM messages to appeal thread ----------

@bot.event
async def on_message(message: discord.Message):
    # Let commands still work
    await bot.process_commands(message)

    if message.author.bot:
        return

    # Only handle DMs from users with active appeals
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


@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(
    member="Member to kick",
    reason="Reason for the kick"
)
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str
):
    # Ensure in main server
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message(
            "This command can only be used in the main server.",
            ephemeral=True
        )
        return

    # Mods+ only
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message(
            "You must be a moderator or admin to use this command.",
            ephemeral=True
        )
        return

    # Optional safety checks
    if member.id == interaction.user.id:
        await interaction.response.send_message(
            "You cannot kick yourself.",
            ephemeral=True
        )
        return
    if member.id == interaction.client.user.id:
        await interaction.response.send_message(
            "I cannot kick myself.",
            ephemeral=True
        )
        return

    # DM with red embed
    try:
        embed = discord.Embed(
            title="You Have Been Kick In Monke Monke Monke League",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason:", value=reason, inline=False)
        await member.send(embed=embed)
    except Exception:
        pass  # can't DM, ignore

    # Kick from guild
    try:
        await member.kick(reason=reason)
    except Exception as e:
        await interaction.response.send_message(
            f"Failed to kick {member.mention}: `{e}`",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"{member.mention} has been **kicked**.\nReason: {reason}",
        ephemeral=True
    )


# /unban command (main server only, mods+ only)

@bot.tree.command(name="unban", description="Unban a user from the main server")
@app_commands.describe(
    user_id="ID of the user to unban (right click -> Copy ID)",
    reason="Reason for the unban (optional)"
)
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def unban(
    interaction: discord.Interaction,
    user_id: str,
    reason: str = "Manual unban"
):
    # Ensure in main server
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message(
            "This command can only be used in the main server.",
            ephemeral=True
        )
        return

    # Mods+ only
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message(
            "You must be a moderator or admin to use this command.",
            ephemeral=True
        )
        return

    # Validate ID
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message(
            "Please provide a valid user ID.",
            ephemeral=True
        )
        return

    # Block unbanning permanent bans via command (optional strictness)
    if uid in permanent_bans:
        await interaction.response.send_message(
            "This user has a **permanent ban** and cannot be unbanned via this command.",
            ephemeral=True
        )
        return

    guild = interaction.guild

    # Try to fetch user for DM
    user = None
    try:
        user = await interaction.client.fetch_user(uid)
    except Exception:
        user = interaction.client.get_user(uid)

    # Unban
    try:
        await guild.unban(discord.Object(id=uid), reason=reason)
    except discord.NotFound:
        await interaction.response.send_message(
            "That user is not currently banned.",
            ephemeral=True
        )
        return
    except Exception as e:
        await interaction.response.send_message(
            f"Failed to unban user: `{e}`",
            ephemeral=True
        )
        return

    # Clear any ban-tracking flags, just in case
    permanent_bans.discard(uid)
    earliest_appeal_time.pop(uid, None)

    # DM the user (if we could fetch them)
    if user is not None:
        try:
            embed = discord.Embed(
                title="You Have Been Unbanned",
                description="[our main server](https://discord.gg/mmml)",
                color=discord.Color.green()
            )
            await user.send(embed=embed)
        except Exception:
            pass

    await interaction.response.send_message(
        f"User with ID `{uid}` has been **unbanned**.\nReason: {reason}",
        ephemeral=True
    )


# /false-ban command (main server only, mods+ only)

@bot.tree.command(name="false-ban", description="Unban a user due to a false ban and notify them.")
@app_commands.describe(
    user_id="ID of the user to unban (right click -> Copy ID)"
)
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def false_ban(
    interaction: discord.Interaction,
    user_id: str,
):
    # Ensure in main server
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
        return

    # Mods+ only
    if not isinstance(interaction.user, discord.Member) or not is_mod_or_admin(interaction.user):
        await interaction.response.send_message("You must be a moderator or admin to use this command.", ephemeral=True)
        return

    # Validate ID
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message(
            "Please provide a valid user ID.",
            ephemeral=True
        )
        return

    # Block unbanning permanent bans via command (optional strictness)
    if uid in permanent_bans:
        await interaction.response.send_message(
            "This user has a **permanent ban** and cannot be unbanned via this command.",
            ephemeral=True
        )
        return

    guild = interaction.guild

    # Try to fetch user for DM
    user = None
    try:
        user = await interaction.client.fetch_user(uid)
    except Exception:
        user = interaction.client.get_user(uid)

    # Unban
    try:
        await guild.unban(discord.Object(id=uid), reason="False ban correction")
    except discord.NotFound:
        await interaction.response.send_message(
            "That user is not currently banned.",
            ephemeral=True
        )
        return
    except Exception as e:
        await interaction.response.send_message(
            f"Failed to unban user: `{e}`",
            ephemeral=True
        )
        return

    # Clear any ban-tracking flags, just in case
    permanent_bans.discard(uid)
    earliest_appeal_time.pop(uid, None)

    # DM the user
    if user is not None:
        try:
            msg = (
                "A False Ban Was Issued! We are very sorry for the inconvenience,\n"
                "[Main Sever](https://discord.gg/mmml)\n"
                "Best regards, MMM Staff Team."
            )
            await user.send(msg)
        except Exception:
            pass

    await interaction.response.send_message(
        f"User with ID `{uid}` has been **unbanned** due to a false ban.",
        ephemeral=True
    )


# ---------- on_ready / sync ----------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")

    # Sync main guild commands (for /submit-report, /kick, /unban, /false-ban)
    main_guild = discord.Object(id=MAIN_GUILD_ID)
    bot.tree.copy_global_to(guild=main_guild)
    await bot.tree.sync(guild=main_guild)

    # Sync appeal guild commands (for /appeal)
    appeal_guild = discord.Object(id=APPEAL_GUILD_ID)
    bot.tree.copy_global_to(guild=appeal_guild)
    await bot.tree.sync(guild=appeal_guild)

    print("Slash commands synced for main and appeal guilds.")


bot.run(os.getenv("TOKEN"))
