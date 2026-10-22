import discord
from discord import app_commands
from discord.ext import commands
import pandas as pd
import sqlite3

# Librerías NBA
from nba_api.stats.static import players, teams
from nba_api.stats.endpoints import playergamelog, teamgamelog, leaguegamefinder

# =========================================================
# 1. BASE DE DATOS Y GESTIÓN DE BANKROLL MULTIUSUARIO
# =========================================================
def init_db():
    conn = sqlite3.connect('bankroll.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id INTEGER PRIMARY KEY,
            bankroll REAL DEFAULT 0.0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS apuestas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            descripcion TEXT,
            monto REAL,
            cuota REAL,
            estado TEXT DEFAULT 'PENDIENTE',
            ganancia REAL DEFAULT 0.0,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# =========================================================
# 2. CONFIGURACIÓN DEL BOT DE DISCORD Y COMANDOS DE BARRA (/)
# =========================================================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

GUILD_ID = 1562367044316565544  # 👈 Pon aquí el ID de tu servidor

@bot.event
async def on_ready():
    print(f'🤖 Bot fijas-bNeT conectado como {bot.user}')
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ ¡Comandos de barra (/) sincronizados al instante! ({len(synced)} comandos registrados).")
    except Exception as e:
        print(f"❌ Error al sincronizar comandos: {e}")

# =========================================================
# 3. SLASH COMMANDS DE BANKROLL
# =========================================================
@bot.tree.command(name="set_bank", description="Configura tu bankroll inicial")
@app_commands.describe(monto="Monto total de tu bankroll")
async def set_bank(interaction: discord.Interaction, monto: float):
    user_id = interaction.user.id
    conn = sqlite3.connect('bankroll.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO usuarios (user_id, bankroll) 
        VALUES (?, ?) 
        ON CONFLICT(user_id) DO UPDATE SET bankroll = ?
    ''', (user_id, monto, monto))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"💰 ¡Bankroll de {interaction.user.mention} configurado en **${monto:,.2f}**!")

@bot.tree.command(name="bank", description="Consulta tu bankroll actual")
async def bank(interaction: discord.Interaction):
    user_id = interaction.user.id
    conn = sqlite3.connect('bankroll.db')
    c = conn.cursor()
    c.execute('SELECT bankroll FROM usuarios WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()

    if row:
        await interaction.response.send_message(f"📊 El bankroll actual de {interaction.user.mention} es **${row[0]:,.2f}**.")
    else:
        await interaction.response.send_message(f"⚠️ {interaction.user.mention}, aún no registras tu bankroll. Usa `/set_bank`.")

@bot.tree.command(name="apuesta", description="Registra una nueva apuesta en tu historial")
@app_commands.describe(monto="Monto apostado", cuota="Cuota de la apuesta", descripcion="Detalle de la jugada")
async def registrar_apuesta(interaction: discord.Interaction, monto: float, cuota: float, descripcion: str):
    user_id = interaction.user.id
    conn = sqlite3.connect('bankroll.db')
    c = conn.cursor()
    
    c.execute('SELECT bankroll FROM usuarios WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    
    if not row or row[0] < monto:
        await interaction.response.send_message(f"❌ {interaction.user.mention}, no tienes suficiente saldo en tu bankroll para esta apuesta.", ephemeral=True)
        conn.close()
        return

    nuevo_bank = row[0] - monto
    c.execute('UPDATE usuarios SET bankroll = ? WHERE user_id = ?', (nuevo_bank, user_id))
    c.execute('''
        INSERT INTO apuestas (user_id, descripcion, monto, cuota)
        VALUES (?, ?, ?, ?)
    ''', (user_id, descripcion, monto, cuota))
    
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ **Apuesta registrada:** {descripcion} | Monto: **${monto:,.2f}** | Cuota: **{cuota}**\n💰 Nuevo Bankroll: **${nuevo_bank:,.2f}**")

# =========================================================
# 4. SLASH COMMAND DE ANÁLISIS NBA (/linea)
# =========================================================
@bot.tree.command(name="linea", description="Analiza estadísticas de un jugador de la NBA")
@app_commands.describe(
    jugador_nombre="Nombre del jugador (ej: LeBron James)",
    estadistica="puntos, rebotes, asistencias, triples, robos, tapones, pra, pr, pa",
    linea_val="Línea de la casa de apuestas (ej: 5.5 o 5,5)",
    ultimos_n="Número de partidos a analizar (por defecto 10)"
)
async def linea(interaction: discord.Interaction, jugador_nombre: str, estadistica: str = "puntos", linea_val: str = "0", ultimos_n: int = 10):
    await interaction.response.defer()

    try:
        # Convertir linea_val sea string o float de origen
        linea_str = str(linea_val).replace(',', '.')
        linea_float = float(linea_str)
    except ValueError:
        linea_float = 0.0

    try:
        player_dict = players.get_players()
        jugadores = [p for p in player_dict if jugador_nombre.lower() in p['full_name'].lower()]

        if not jugadores:
            await interaction.followup.send(f"❌ No encontré al jugador **'{jugador_nombre}'**.")
            return

        jugador = jugadores[0]
        player_id = jugador['id']

        # 1. Buscar temporada actual (2026-27)
        try:
            gamelog_actual = playergamelog.PlayerGameLog(player_id=player_id, season='2026-27')
            df = gamelog_actual.get_data_frames()[0]
        except Exception:
            df = pd.DataFrame()

        # 2. Fallback con temporada anterior (2025-26) si el tamaño de muestra es < ultimos_n
        if len(df) < ultimos_n:
            try:
                gamelog_prev = playergamelog.PlayerGameLog(player_id=player_id, season='2025-26')
                df_prev = gamelog_prev.get_data_frames()[0]
                df = pd.concat([df, df_prev], ignore_index=True)
            except Exception:
                pass

        if df.empty:
            await interaction.followup.send(f"⚠️ No se encontraron partidos registrados para **{jugador['full_name']}**.")
            return

        df = df.head(ultimos_n)

        # Métricas compuestas
        df['PRA'] = df['PTS'] + df['REB'] + df['AST']
        df['PR'] = df['PTS'] + df['REB']
        df['PA'] = df['PTS'] + df['AST']

        col_map = {
            'puntos': 'PTS', 'pts': 'PTS',
            'rebotes': 'REB', 'reb': 'REB',
            'asistencias': 'AST', 'ast': 'AST',
            'triples': 'FG3M', 'robos': 'STL',
            'tapones': 'BLK', 'bloqueos': 'BLK', 'pra': 'PRA',
            'pr': 'PR', 'pa': 'PA'
        }

        stat_col = col_map.get(estadistica.lower(), 'PTS')
        promedio = df[stat_col].mean()

        if linea_float > 0:
            over_hits = len(df[df[stat_col] > linea_float])
            under_hits = len(df[df[stat_col] < linea_float])
            porcentaje_over = (over_hits / len(df)) * 100

            respuesta = (
                f"📊 **Análisis de Línea para {jugador['full_name']}** (Últimos {len(df)} partidos)\n"
                f"• **Estadística:** {estadistica.upper()}\n"
                f"• **Línea Evaluada:** {linea_float}\n"
                f"• **Promedio:** {promedio:.1f}\n"
                f"• **Over ({linea_float}):** {over_hits}/{len(df)} partidos ({porcentaje_over:.1f}%)\n"
                f"• **Under ({linea_float}):** {under_hits}/{len(df)} partidos ({100 - porcentaje_over:.1f}%)"
            )
        else:
            respuesta = (
                f"📊 **Promedio de {jugador['full_name']}** (Últimos {len(df)} partidos)\n"
                f"• **Estadística:** {estadistica.upper()}\n"
                f"• **Promedio Registrado:** {promedio:.1f}"
            )

        await interaction.followup.send(respuesta)

    except Exception as e:
        await interaction.followup.send(f"❌ Ocurrió un error al procesar la solicitud: {e}")
# =========================================================
# SLASH COMMAND DE LÍNEA DE PARTIDO COMPLETO (/linea_partido)
# =========================================================
@bot.tree.command(name="linea_partido", description="Analiza el total de puntos entre dos equipos")
@app_commands.describe(
    equipo_a="Primer equipo (ej: Bucks, MIL, Wizards, WAS)",
    equipo_b="Segundo equipo (ej: Wizards, WAS, Bucks, MIL)",
    linea_val="Línea total de puntos de la casa (ej: 246,5)",
    ultimos_n="Número de partidos directos a analizar (por defecto 10)"
)
async def linea_partido(interaction: discord.Interaction, equipo_a: str, equipo_b: str, linea_val: str, ultimos_n: int = 10):
    await interaction.response.defer()

    try:
        linea_float = float(str(linea_val).replace(',', '.'))
    except ValueError:
        await interaction.followup.send("❌ Formato de línea inválido. Usa comas o puntos (ej: 246,5).")
        return

    try:
        all_teams = teams.get_teams()
        
        # Buscar ID del Equipo A
        team_a_match = [t for t in all_teams if equipo_a.lower() in t['full_name'].lower() or equipo_a.upper() == t['abbreviation']]
        # Buscar ID del Equipo B
        team_b_match = [t for t in all_teams if equipo_b.lower() in t['full_name'].lower() or equipo_b.upper() == t['abbreviation']]

        if not team_a_match or not team_b_match:
            await interaction.followup.send("❌ No se encontró alguno de los equipos. Verifica los nombres (ej: Bucks, Wizards, LAL, BOS).")
            return

        t_a = team_a_match[0]
        t_b = team_b_match[0]

        # Buscar partidos entre ambos equipos usando leaguegamefinder
        gamefinder = leaguegamefinder.LeagueGameFinder(team_id_nullable=t_a['id'], timeout=10)
        games = gamefinder.get_data_frames()[0]

        # Filtrar enfrentamientos contra el equipo B
        vs_games = games[games['MATCHUP'].str.contains(t_b['abbreviation'], case=False, na=False)].head(ultimos_n)

        if vs_games.empty:
            await interaction.followup.send(f"⚠️ No se encontraron enfrentamientos recientes entre **{t_a['full_name']}** y **{t_b['full_name']}**.")
            return

        # Calcular puntos totales del partido (PTS anotados + PTS recibidos)
        vs_games['TOTAL_PTS'] = vs_games['PTS'] + (vs_games['PTS'] - vs_games['PLUS_MINUS'])

        promedio_puntos = vs_games['TOTAL_PTS'].mean()
        over_hits = len(vs_games[vs_games['TOTAL_PTS'] > linea_float])
        under_hits = len(vs_games[vs_games['TOTAL_PTS'] < linea_float])
        total_juegos = len(vs_games)
        porcentaje_over = (over_hits / total_juegos) * 100

        respuesta = (
            f"🏀 **Análisis de Puntos Totales: {t_a['full_name']} vs {t_b['full_name']}** (Últimos {total_juegos} H2H)\n"
            f"• **Línea Evaluada:** {linea_float}\n"
            f"• **Promedio de Puntos Comb.:** {promedio_puntos:.1f}\n"
            f"• **Over ({linea_float}):** {over_hits}/{total_juegos} partidos ({porcentaje_over:.1f}%)\n"
            f"• **Under ({linea_float}):** {under_hits}/{total_juegos} partidos ({100 - porcentaje_over:.1f}%)"
        )

        await interaction.followup.send(respuesta)

    except Exception as e:
        await interaction.followup.send(f"❌ Error al consultar la línea de partido: {e}")
# =========================================================
# 5. EJECUCIÓN DEL BOT
# =========================================================
# REEMPLAZA CON TU TOKEN REAL
TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)