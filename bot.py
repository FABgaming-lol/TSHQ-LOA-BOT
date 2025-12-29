import discord
from discord.ext import commands, tasks
import sqlite3
import datetime
import re

# ==========================================
# CONFIGURATION
# ==========================================

# EXAMPLE TOKEN (Replace this with your real token from Discord Developer Portal)
TOKEN = 'MTQ1NTI0Mzc2OTA4NjYwNzQ1Mg.Gqkhdn.96rnEvpr-CzWJAVRvMSQFxgoZi0Wbz4gRCL5Qc'

# The ID of the role that designates a user is on leave
LOA_ROLE_ID = 1450299129480745010

# List of Role IDs allowed to add/remove LOAs (Staff/Managers)
MANAGER_ROLE_IDS = [
    1430355828527071343, 
    1430355830498394152, 
    1455020456305492002
]

# The ID of the channel where the bot will post logs
LOG_CHANNEL_ID = 1453756182375170051

# ==========================================
# DATABASE SETUP (SQLite)
# ==========================================

conn = sqlite3.connect('loa_database.db')
c = conn.cursor()
# Create table if it doesn't exist to store User ID, Start Date, End Date, and Reason
c.execute('''CREATE TABLE IF NOT EXISTS loas (
                user_id INTEGER PRIMARY KEY,
                start_date TEXT,
                end_date TEXT,
                reason TEXT
            )''')
conn.commit()

# ==========================================
# BOT SETUP
# ==========================================

intents = discord.Intents.default()
intents.members = True # CRITICAL: Required to assign roles
intents.message_content = True # CRITICAL: Required to read commands

bot = commands.Bot(command_prefix='!', intents=intents)

# Helper function to parse duration (e.g., "7d" -> 7 days from now)
def parse_duration(duration_str):
    regex = re.match(r"(\d+)([dwm])", duration_str.lower())
    if not regex:
        return None
    
    amount = int(regex.group(1))
    unit = regex.group(2)
    
    now = datetime.datetime.now()
    
    if unit == 'd': # Days
        return now + datetime.timedelta(days=amount)
    elif unit == 'w': # Weeks
        return now + datetime.timedelta(weeks=amount)
    elif unit == 'm': # Months (approx 30 days)
        return now + datetime.timedelta(days=amount*30)
    return None

# Helper function to check if user has permission
def is_manager(user):
    user_role_ids = [role.id for role in user.roles]
    # Check if the user has ANY of the manager IDs defined in config
    return any(manager_id in user_role_ids for manager_id in MANAGER_ROLE_IDS)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print(f'Bot ID: {bot.user.id}')
    # Start the background task to check for expired LOAs
    check_expired_loas.start() 
    print('LOA Tracking System Started.')

# ==========================================
# COMMANDS
# ==========================================

@bot.command(name='loa')
async def add_loa(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """
    Puts a user on LOA.
    Usage: !loa @User 7d Personal Reasons
    """
    # 1. Check Permissions
    if not is_manager(ctx.author):
        await ctx.send("⛔ You do not have permission to manage leaves.")
        return

    # 2. Parse Duration
    end_date = parse_duration(duration)
    if not end_date:
        await ctx.send("❌ Invalid duration format. Use `d` for days, `w` for weeks, `m` for months.\nExample: `!loa @User 7d Vacation`")
        return

    # 3. Add to Database
    start_date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    end_date_str = end_date.strftime("%Y-%m-%d %H:%M:%S")

    try:
        c.execute("INSERT OR REPLACE INTO loas (user_id, start_date, end_date, reason) VALUES (?, ?, ?, ?)",
                  (member.id, start_date_str, end_date_str, reason))
        conn.commit()
    except Exception as e:
        await ctx.send(f"❌ Database error: {e}")
        return

    # 4. Add the LOA Role
    loa_role = ctx.guild.get_role(LOA_ROLE_ID)
    if loa_role:
        try:
            await member.add_roles(loa_role)
        except discord.Forbidden:
            await ctx.send("❌ **Error:** I cannot add the role. Please ensure my Bot Role is positioned **HIGHER** than the LOA role in Server Settings.")
            return
    else:
        await ctx.send(f"❌ Role with ID `{LOA_ROLE_ID}` not found in this server.")
        return

    # 5. Send Confirmation
    embed = discord.Embed(title="✅ LOA Activated", color=discord.Color.green())
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Return Date", value=f"<t:{int(end_date.timestamp())}:D>", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    
    await ctx.send(embed=embed)

    # 6. Log to Channel
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"📝 **LOA STARTED**: {member.mention} put on leave by {ctx.author.mention}.\n**Ends:** {end_date_str}\n**Reason:** {reason}")
    else:
        print(f"Warning: Log channel ID {LOG_CHANNEL_ID} not found.")

@bot.command(name='endloa')
async def end_loa(ctx, member: discord.Member):
    """
    Manually ends a user's LOA.
    Usage: !endloa @User
    """
    # Check Permissions
    if not is_manager(ctx.author):
        await ctx.send("⛔ You do not have permission to manage leaves.")
        return

    # Remove from DB
    c.execute("DELETE FROM loas WHERE user_id = ?", (member.id,))
    conn.commit()

    # Remove Role
    loa_role = ctx.guild.get_role(LOA_ROLE_ID)
    if loa_role and loa_role in member.roles:
        try:
            await member.remove_roles(loa_role)
            await ctx.send(f"✅ LOA ended manually for {member.mention}.")
        except discord.Forbidden:
            await ctx.send("❌ I couldn't remove the role. Check role hierarchy.")
    else:
        await ctx.send(f"⚠️ {member.mention} is removed from the database, but did not have the LOA role.")

    # Log
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"🛑 **LOA ENDED MANUALLY**: {member.mention} removed by {ctx.author.mention}.")

@bot.command(name='active_loas')
async def list_loas(ctx):
    """
    Lists all users currently on leave.
    Usage: !active_loas
    """
    # Only allow managers to see the list? (Optional, currently allowed for everyone)
    # If you want to restrict this too, uncomment the next two lines:
    # if not is_manager(ctx.author):
    #     return await ctx.send("⛔ Permission denied.")

    c.execute("SELECT user_id, end_date, reason FROM loas")
    rows = c.fetchall()

    if not rows:
        await ctx.send("📂 No active leaves found.")
        return

    embed = discord.Embed(title="📅 Active Leaves of Absence", color=discord.Color.blue())
    
    count = 0
    for row in rows:
        user_id, end_date_str, reason = row
        member = ctx.guild.get_member(user_id)
        
        # Parse DB string back to timestamp for nice Discord formatting
        try:
            dt_obj = datetime.datetime.strptime(end_date_str, "%Y-%m-%d %H:%M:%S")
            time_display = f"<t:{int(dt_obj.timestamp())}:R>" # e.g. "in 5 days"
        except:
            time_display = end_date_str

        name = member.display_name if member else f"Unknown User ({user_id})"
        embed.add_field(name=f"👤 {name}", value=f"**Ends:** {time_display}\n**Reason:** {reason}", inline=False)
        count += 1
        
        if count >= 25: # Embed limit
            embed.set_footer(text="List truncated due to Discord embed limits.")
            break

    await ctx.send(embed=embed)

# ==========================================
# BACKGROUND TASK (AUTO REMOVE)
# ==========================================

@tasks.loop(minutes=1)
async def check_expired_loas():
    if not bot.is_ready():
        return

    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Select users whose end_date has passed
    c.execute("SELECT user_id FROM loas WHERE end_date < ?", (current_time,))
    expired_users = c.fetchall()

    if expired_users:
        for guild in bot.guilds:
            loa_role = guild.get_role(LOA_ROLE_ID)
            log_channel = guild.get_channel(LOG_CHANNEL_ID)

            for row in expired_users:
                user_id = row[0]
                member = guild.get_member(user_id)

                # Remove from DB
                c.execute("DELETE FROM loas WHERE user_id = ?", (user_id,))
                conn.commit()

                if member:
                    # Remove Role
                    if loa_role and loa_role in member.roles:
                        try:
                            await member.remove_roles(loa_role)
                            print(f"Removed expired LOA for {member.name}")
                            
                            # Log expiration
                            if log_channel:
                                await log_channel.send(f"⏰ **LOA EXPIRED**: {member.mention}'s leave has ended automatically.")
                        except discord.Forbidden:
                            print(f"Could not remove role for {member.name} - Permission Issue")

bot.run(TOKEN)