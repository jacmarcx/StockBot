import discord
from src.functions import *
from discord.ext import commands
from pretty_help import PrettyHelp
from src.util.Embedder import *
from src.util.SentryHelper import uncaught
from src.positions import buy_position, sell_position, get_portfolio, NoPositionsException
from src.database import Session, connect
import sentry_sdk
import asyncio

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
SENTRY_DSN = os.getenv("SENTRY_DSN")
dev_prefix = os.getenv("DEV_PREFIX")
prefix = "!" if not dev_prefix else dev_prefix
bot = commands.Bot(command_prefix=prefix, help_command=PrettyHelp(no_category='Commands'))


@bot.command(
    help="Requires no arguments, just checks for the top gainers, losses and volume in the US. e.g. !movers",
    brief="Returns the top gainers, losses and volume from the US.")
async def movers(ctx):
    day_gainers, day_losers, top_volume = getMovers()
    await ctx.send(embed=day_gainers)
    await ctx.send(embed=day_losers)
    await ctx.send(embed=top_volume)


@bot.command(
    help="Requires two arguments, ticker and region. Default region used"
         " is US. Example of command: !info BB CA or !info TSLA",
    brief="Returns a market summary of the specified ticker.")
async def info(ctx, arg1, arg2='US'):
    keys = ['Opening Price', 'Current Price', 'Day High',
            'Day Low', '52 Week High', '52 Week Low']
    stock_details, name = getDetails(str(arg1), str(arg2))
    embed = discord.Embed(title="Information on " + name, colour=Colour.green())
    if len(stock_details) == 9:
        for key, value in stock_details.items():
            if key in keys:
                embed.add_field(name=key, value="$" + value, inline=True)
                continue
            else:
                embed.add_field(name=key, value=value, inline=True)
        await ctx.send(embed=embed)

    else:
        for key, value in stock_details.items():
            if key in keys:
                embed.add_field(name=key, value="$" + value, inline=True)
                continue
            if key == 'Annual Div Rate':
                embed.add_field(name=key, value="$" + value + " per share", inline=True)
            else:
                embed.add_field(name=key, value=value, inline=True)
        await ctx.send(embed=embed)


@bot.command(
    help="Requires one argument, ticker. Example !news TSLA",
    brief="Returns recent news related to the specified ticker")
async def news(ctx, arg1, *args):
    res, titles = getNews(str(arg1))
    i = 0
    for key, value in res.items():
        embed = discord.Embed(title=str(titles[i]), url=str(value),
                              description=str(key),
                              color=discord.Color.blue())
        i += 1
        await ctx.send(embed=embed)


@bot.command(
    help="Requires one argument ticker and one optional argument region (specifically for Canada). Example !live TSLA "
         "or !live BB CA",
    brief="Returns the live price of the ticker")
async def live(ctx, arg1, *args):
    if len(args) == 1 and args[0].upper() == 'CA':
        currency = "CAD"
        if ('.V' in arg1.upper()) or ('.NE' in arg1.upper()) or ('.TO' in arg1.upper()):
            price = live_stock_price(str(arg1))
            embed = Embedder.embed(title=f"{str(arg1).upper()}", message=f"${price} {currency}")
        else:
            price, suffix = findSuffix(str(arg1))
            embed = Embedder.embed(title=f"{str(arg1).upper()}{suffix}", message=f"${price} {currency}")
    elif ('.V' in arg1.upper()) or ('.NE' in arg1.upper()) or ('.TO' in arg1.upper()):
        currency = "CAD"
        price = live_stock_price(str(arg1))
        embed = Embedder.embed(title=f"{str(arg1).upper()}", message=f"${price} {currency}")
    else:
        currency = "USD"
        price = live_stock_price(str(arg1))
        embed = Embedder.embed(title=f"{str(arg1).upper()}", message=f"${price} {currency}")
    await ctx.send(embed=embed)


@bot.command(
    help="Requires one argument ticker and one optional argument region (specifically for Canada) and one argument number of days. Example !hist TSLA 45"
         "or !live BB CA 14",
    brief="Returns info regarding increase or decrease in stock price in the last x days")
async def hist(ctx, arg1, *args):
    if args[0].isdigit():
        # No region specified, default to US
        stockResult = getHistoricalData(arg1, 'US', args[0])
        marker = '' if stockResult['PriceChange'] < 0 else '+'
        currency, pricediff, percentdiff = stockResult['Currency'], \
                                           stockResult['PriceChange'], stockResult['PriceChangePercentage']
        embed = Embedder.embed(title=f"{(arg1.upper())} Performance In The Last {str(args[0])} Days:",
                               message=f"{marker}${pricediff:.2f} {currency} "
                                       f"({marker}{percentdiff:.2f}%)")
        await ctx.send(embed=embed)

    else:
        # Region is specified, so there should be 2 arguments: region and number of days
        suffix = findSuffix(arg1)[1]
        stockResult = getHistoricalData(arg1, args[0].upper(), args[1])
        marker = '' if stockResult['PriceChange'] < 0 else '+'
        currency, pricediff, percentdiff = stockResult['Currency'], stockResult['PriceChange'], stockResult[
            'PriceChangePercentage']
        embed = Embedder.embed(title=f"{(arg1.upper())}{suffix} Performance In The Last {str(args[1])} Days:",
                               message=f"{marker}${pricediff:.2f} {currency} "
                                       f"({marker}{percentdiff:.2f}%)")

        await ctx.send(embed=embed)


@bot.command(
    help="Requires two arguments, ticker and price. Example !alert TSLA 800",
    brief="Directly messages the user when the price hits the threshold indicated so they can buy/sell."
)
async def alert(ctx, ticker, price):
    if float(live_stock_price(ticker) > float(price)):
        while True:
            print(live_stock_price(ticker))
            if float(live_stock_price(ticker)) <= float(price):
                await ctx.author.send("```" + str(ticker).upper() + " has hit your price point of $" + price + ".```")
                break
            await asyncio.sleep(10)
    else:
        while True:
            if float(live_stock_price(ticker)) >= float(price):
                await ctx.author.send("```" + str(ticker).upper() + " has hit your price point of $" + price + ".```")
                break
            await asyncio.sleep(10)


@bot.command()
async def buy(ctx, ticker: str, amount: int, price: float = None):
    session = Session()
    ticker_price, total, currency = calculate_total(ticker=ticker, amount=amount, price=price)
    ticker = ticker.upper()
    is_usd = True if currency == "USD" else False
    user_id = str(ctx.message.author.id)
    username = ctx.message.author.name
    buy_complete = buy_position(session=session, user_id=user_id, username=username,
                                symbol=ticker, amount=amount, price=ticker_price, is_usd=is_usd)
    if buy_complete:
        embed = Embedder.embed(title=f"Successfully bought ${ticker}",
                               message=f"{ticker} x {amount} @{ticker_price} {currency}\n"
                                       f"`Total: ${'{:.2f}'.format(total)}  {currency}`")
    else:
        embed = Embedder.error("Something went wrong.")
    await ctx.send(embed=embed)


@bot.command()
async def sell(ctx, ticker: str, amount: int, price: float = None):
    session = Session()
    ticker_price, total, currency = calculate_total(ticker=ticker, amount=amount, price=price)
    ticker = ticker.upper()
    user_id = str(ctx.message.author.id)
    username = ctx.message.author.name
    sell_complete = sell_position(session=session, user_id=user_id, username=username,
                                  symbol=ticker, amount=amount, price=ticker_price)
    if sell_complete:
        embed = Embedder.embed(title=f"Successfully Sold ${ticker}",
                               message=f"{ticker} x {amount} @{ticker_price} {currency}\n"
                                       f"`Total: ${'{:.2f}'.format(total)}  {currency}`")
    else:
        embed = Embedder.error("Check if you have enough positions to sell!")
    await ctx.send(embed=embed)


@bot.command()  # TODO: add profit/loss for portfolio summary
async def portfolio(ctx, mobile=""):
    if mobile and mobile not in ("m", "mobile"):
        raise discord.ext.commands.BadArgument
    session = Session()
    user_id = ctx.author.id
    username = ctx.author.name
    mobile = bool(mobile)
    portfolio_complete = get_portfolio(session=session, user_id=user_id, username=username, mobile=mobile)
    if portfolio_complete and mobile:
        await ctx.send(embed=portfolio_complete[0])
        await ctx.send(embed=portfolio_complete[1])
    else:
        await ctx.send(f"""```{portfolio_complete[0]}```""")
        await ctx.send(f"""```{portfolio_complete[1]}```""")
    if not portfolio_complete:
        await ctx.send(Embedder.error(""))


@info.error
async def info_error(ctx, error):
    if isinstance(error, commands.CommandError):
        msg = """
        Came across an error while processing your request.
        Check if your region corresponds to the proper exchange,
        or re-check the ticker you used.
        """
    else:
        msg = uncaught(error)
    await ctx.send(embed=Embedder.error(msg))


@news.error
async def news_error(ctx, error):
    if isinstance(error, commands.CommandError):
        msg = 'Came across an error while processing your request.'
    else:
        msg = uncaught(error)
    await ctx.send(embed=Embedder.error(msg))


@live.error
async def live_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        msg = """
        Came across an error while processing your request.
        Check if your region corresponds to the proper exchange,
        or re-check the ticker you used.
        """
    else:
        msg = uncaught(error)
    await ctx.send(embed=Embedder.error(msg))


@alert.error
async def alert_error(ctx, error):
    if isinstance(error, commands.CommandError):
        msg = 'Came across an error while processing your request. Please check your ticker again.'
    else:
        msg = uncaught(error)
    await ctx.send(embed=Embedder.error(msg))


@buy.error
async def buy_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        msg = "Bad argument;\n`!buy [ticker (KBO)] [amount (13)] [price (12.50)(optional)]`"
    elif isinstance(error, commands.CommandInvokeError):
        msg = "Invalid ticker."
    elif isinstance(error, commands.MissingRequiredArgument):
        msg = "Missing arguments;\n`!buy [ticker (KBO)] [amount (13)] [price (12.50)(optional)]`"
    else:
        msg = uncaught(error)
    await ctx.send(embed=Embedder.error(msg))


@sell.error
async def sell_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        msg = "Bad argument;\n`!sell [ticker (KBO)] [amount (13)] [price (12.50)]`"
    elif isinstance(error, commands.CommandInvokeError):
        msg = "Invalid ticker."
    else:
        msg = uncaught(error)
    await ctx.send(embed=Embedder.error(msg))


@portfolio.error
async def portfolio_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        msg = "Bad argument;\n`!portfolio [m or mobile (for mobile view)]`"
    elif isinstance(error, NoPositionsException):
        msg = "No position was found with the user!\nTry `!buy` command first to add positions."
    else:
        msg = uncaught(error)
    await ctx.send(embed=Embedder.error(msg))


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return await ctx.send(embed=Embedder.error("Command does not exist."))
    elif hasattr(ctx.command, "on_error"):
        return
    else:
        msg = uncaught(error)
    return await ctx.send(embed=Embedder.error(msg))


@bot.event
async def on_ready():
    sentry_sdk.init(
        SENTRY_DSN,
        traces_sample_rate=1.0
    )
    connect(DATABASE_URL)
    print("We are online!")
    print("Name: {}".format(bot.user.name))
    print("ID: {}".format(bot.user.id))

bot.run(TOKEN)
