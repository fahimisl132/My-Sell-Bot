import csv, io, hashlib, logging
from decimal import Decimal, InvalidOperation
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from .config import BOT_TOKEN, OWNER_ID, SUPPORT_USERNAME
from .db import Session, User, Admin, Product, Stock, Deposit, Order, StockBatch, StockPayload, ProductStockSchema, init_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
WAIT = {}
PERMS = {"products":"Products", "stock":"Stock", "deposits":"Deposits", "orders":"Orders", "users":"Users", "stats":"Statistics"}

def money(c): return f"${c/100:.2f}"
def markup(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t,d in row] for row in rows])
def owner(uid): return uid == OWNER_ID
async def permissions(uid):
    if owner(uid): return set(PERMS)
    async with Session() as s:
        a = await s.get(Admin, uid)
        return set(a.permissions.split(",")) if a and a.active else set()
async def can(uid, key): return key in await permissions(uid)

def customer_menu():
    return markup([[("🛍️ Buy Products","shop:0"),("👤 My Profile","profile")],
                   [("💰 Add Balance","deposit"),("📦 My Orders","orders")],
                   [("👥 Refer","refer"),("🎧 Support","support")],
                   [("🏪 Apply as Reseller","reseller")]])
def admin_menu():
    return markup([[("👨‍💼 Admin Management","admins"),("📦 Product Management","products")],
                   [("💰 Deposits","deposits"),("📊 Statistics","stats")],
                   [("⬅️ Customer Menu","home")]])

async def show_permission_picker(q, selected, target):
    rows=[]
    for key,label in PERMS.items():
        rows.append([("✅ " if selected.get(key) else "⬜ ")+label, f"perm:{key}:{target}"])
    rows.append([("💾 Save Permissions",f"saveadmin:{target}")])
    rows.append([("⬅️ Cancel","adminlist")])
    await q.edit_message_text(
        f"🔐 ADMIN PERMISSIONS\nUser ID: {target}\n\nপ্রয়োজনীয় Permission বেছে নিন।",
        reply_markup=markup(rows))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    async with Session() as s:
        user=await s.get(User,u.id)
        if not user:
            s.add(User(user_id=u.id,name=u.full_name,username=u.username or ""))
        await s.commit()
    await update.message.reply_text("স্বাগতম! নিচের Menu ব্যবহার করুন।",reply_markup=customer_menu())

async def shop(q, parent=0):
    async with Session() as s:
        stmt=select(Product).where(Product.active.is_(True))
        stmt=stmt.where(Product.parent_id.is_(None)) if parent==0 else stmt.where(Product.parent_id==parent)
        items=(await s.scalars(stmt.order_by(Product.id))).all()
        par=await s.get(Product,parent) if parent else None
    rows=[]
    for p in items:
        label=("📁 " if p.kind=="FOLDER" else "📦 ")+p.name
        if p.kind=="SELLABLE": label += " — "+money(p.price)
        rows.append([(label,f"item:{p.id}")])
    rows.append([("⬅️ Back",f"shop:{par.parent_id or 0}" if par else "home")])
    await q.edit_message_text("🛍️ "+(par.name if par else "BUY PRODUCTS"),reply_markup=markup(rows))

async def admin_product_menu(q, uid):
    if not await can(uid,"products"): return await q.answer("Permission নেই",show_alert=True)
    await q.edit_message_text("PRODUCT MANAGEMENT",reply_markup=markup([
        [("➕ Add Main Product","addroot"),("📂 Product List","roots")],
        [("⬅️ Admin Panel","admin")]]))

async def manage_product(q, uid, pid):
    if not await can(uid,"products"): return await q.answer("Permission নেই",show_alert=True)
    async with Session() as s: p=await s.get(Product,pid)
    if not p:
        return await q.answer("Product পাওয়া যায়নি।",show_alert=True)
    await q.edit_message_text(f"{p.name}\nType: {p.kind}\nPrice: {money(p.price)}",reply_markup=markup([
        [("➕ Add Folder","child:"+str(pid)+":FOLDER"),("➕ Add Sellable","child:"+str(pid)+":SELLABLE")],
        [("✏️ Rename","rename:"+str(pid)),("💵 Set Price","price:"+str(pid))],
        [("📥 Manage Stock","stock:"+str(pid)),("🙈 Hide","hide:"+str(pid))],
        [("⬅️ Back","roots")]]))


async def stock_menu(q, pid):
    async with Session() as s:
        p=await s.get(Product,pid)
        schema=await s.get(ProductStockSchema,pid)
    if not p:
        return await q.edit_message_text("Product পাওয়া যায়নি।")
    fmt=(schema.format_text[:700] if schema and schema.format_text else "Not set")
    await q.edit_message_text(
        f"📦 MANAGE STOCK\nProduct: {p.name}\nStock Format: {fmt}",
        reply_markup=markup([
            [("📄 Upload CSV File","csv:"+str(pid))],
            [("✍️ Manual Stock Entry","manual:"+str(pid))],
            [("📊 View Current Stock","viewstock:"+str(pid))],
            [("🗑️ Delete Available Stock","delstock:"+str(pid))],
            [("🕘 Stock History","stockhistory:"+str(pid))],
            [("🧾 Set Product Stock Format","stockschema:"+str(pid))],
            [("⬅️ Back","manage:"+str(pid))]
        ]))

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; d=q.data
    try:
        if d=="home": await q.edit_message_text("MAIN MENU",reply_markup=customer_menu())
        elif d=="profile":
            async with Session() as s: u=await s.get(User,uid)
            if not u:
                return await q.answer("প্রথমে /start দিন।",show_alert=True)
            await q.edit_message_text(f"👤 PROFILE\nName: {u.name}\nID: {uid}\nBalance: {money(u.balance)}",reply_markup=markup([[("⬅️ Back","home")]]))
        elif d=="support": await q.edit_message_text("Support: "+SUPPORT_USERNAME,reply_markup=markup([[("⬅️ Back","home")]]))
        elif d in ("refer","reseller"): await q.edit_message_text("এই ফিচারটি পরবর্তী ধাপে সম্পূর্ণ করা হবে।",reply_markup=markup([[("⬅️ Back","home")]]))
        elif d=="orders":
            async with Session() as s: orders=(await s.scalars(select(Order).where(Order.user_id==uid).order_by(Order.id.desc()).limit(20))).all()
            msg="📦 MY ORDERS\n"+("\n".join(f"#{o.id} {o.product_name} ×{o.qty} {money(o.total)}" for o in orders) if orders else "কোনো Order নেই")
            await q.edit_message_text(msg,reply_markup=markup([[("⬅️ Back","home")]]))
        elif d=="deposit":
            WAIT[uid]=("deposit",None)
            await q.edit_message_text("Deposit দিতে লিখুন: Amount, Transaction ID\nউদাহরণ: 10, TX12345")
        elif d.startswith("shop:"): await shop(q,int(d.split(":")[1]))
        elif d.startswith("item:"):
            pid=int(d.split(":")[1])
            async with Session() as s:
                p=await s.get(Product,pid)
                if not p or not p.active:
                    return await q.answer("Product পাওয়া যায়নি।",show_alert=True)
                stock_count=await s.scalar(select(func.count()).select_from(Stock).where(Stock.product_id==pid,Stock.state=="AVAILABLE")) if p.kind=="SELLABLE" else 0
            if p.kind=="FOLDER": await shop(q,pid)
            elif p.kind=="SELLABLE": await q.edit_message_text(f"{p.name}\nPrice: {money(p.price)}\nAvailable: {stock_count}",reply_markup=markup([[("Buy 1","buy:"+str(pid)+":1"),("Buy 5","buy:"+str(pid)+":5")],[("⬅️ Back",f"shop:{p.parent_id or 0}")]]))
        elif d.startswith("buy:"):
            _,pid,qty=d.split(":"); pid=int(pid); qty=int(qty)
            async with Session() as s:
                p=await s.get(Product,pid); u=await s.get(User,uid)
                if not p or not p.active or p.kind!="SELLABLE":
                    return await q.answer("Product পাওয়া যায়নি।",show_alert=True)
                if not u:
                    return await q.answer("প্রথমে /start দিন।",show_alert=True)
                if qty not in (1,5):
                    return await q.answer("Quantity সঠিক নয়।",show_alert=True)
                items=(await s.scalars(select(Stock).where(Stock.product_id==pid,Stock.state=="AVAILABLE").order_by(Stock.id).limit(qty).with_for_update())).all()
                total=p.price*qty
                if len(items)<qty: return await q.answer("Stock কম আছে",show_alert=True)
                if u.balance<total: return await q.answer("Balance কম। আগে Add Balance করুন।",show_alert=True)
                delivery="\n\n---\n\n".join(x.raw_text for x in items)
                payloads={}
                for item in items:
                    payloads[item.id]=await s.scalar(select(StockPayload).where(StockPayload.stock_id==item.id))
                u.balance-=total
                for item in items: item.state="SOLD"
                s.add(Order(user_id=uid,product_name=p.name,qty=qty,total=total,delivery=delivery))
                await s.commit()
            await q.edit_message_text(f"✅ Purchase Successful\nProduct: {p.name}\nQuantity: {qty}\nTotal: {money(total)}\nDelivery আলাদা Message/File হিসেবে পাঠানো হচ্ছে।",
                reply_markup=customer_menu())
            for item in items:
                payload=payloads.get(item.id)
                if payload and payload.mode=="CSV" and payload.header_bytes is not None and payload.record_bytes is not None:
                    file_obj=io.BytesIO(payload.header_bytes+payload.record_bytes)
                    await context.bot.send_document(chat_id=uid,document=file_obj,filename=f"stock_{item.id}.csv",
                        caption=f"আপনার পণ্যের CSV Stock #{item.id}")
                else:
                    await context.bot.send_message(chat_id=uid,text=item.raw_text[:4000])
        elif d=="admin":
            if not await permissions(uid): return await q.answer("Admin access নেই",show_alert=True)
            await q.edit_message_text("⚙️ ADMIN PANEL",reply_markup=admin_menu())
        elif d=="products": await admin_product_menu(q,uid)
        elif d=="addroot":
            if not await can(uid,"products"): return await q.answer("Permission নেই",show_alert=True)
            WAIT[uid]=("addroot",None); await q.edit_message_text("Main Product-এর নাম লিখুন:")
        elif d=="roots":
            if not await can(uid,"products"): return await q.answer("Permission নেই",show_alert=True)
            async with Session() as s: items=(await s.scalars(select(Product).where(Product.parent_id.is_(None),Product.active.is_(True)))).all()
            rows=[[(p.name,"manage:"+str(p.id))] for p in items]+[[("⬅️ Back","products")]]
            await q.edit_message_text("Product List",reply_markup=markup(rows))
        elif d.startswith("manage:"): await manage_product(q,uid,int(d.split(":")[1]))
        elif d.startswith("child:"):
            if not await can(uid,"products"): return await q.answer("Permission নেই",show_alert=True)
            _,pid,kind=d.split(":"); WAIT[uid]=("child",(int(pid),kind)); await q.edit_message_text("নতুন Option-এর নাম লিখুন:")
        elif d.startswith("rename:"):
            if not await can(uid,"products"): return await q.answer("Permission নেই",show_alert=True)
            pid=int(d.split(":")[1]); WAIT[uid]=("rename",pid); await q.edit_message_text("নতুন নাম লিখুন:")
        elif d.startswith("price:"):
            if not await can(uid,"products"): return await q.answer("Permission নেই",show_alert=True)
            pid=int(d.split(":")[1]); WAIT[uid]=("price",pid); await q.edit_message_text("USD Price লিখুন, যেমন 2.50")
        elif d.startswith("stock:"):
            pid=int(d.split(":")[1])
            if not await can(uid,"stock"): return await q.answer("Permission নেই",show_alert=True)
            await stock_menu(q, pid)
        elif d.startswith("viewstock:"):
            if not await can(uid,"stock"): return await q.answer("Permission নেই",show_alert=True)
            pid=int(d.split(":")[1])
            async with Session() as s:
                p=await s.get(Product,pid)
                available=await s.scalar(select(func.count()).select_from(Stock).where(Stock.product_id==pid,Stock.state=="AVAILABLE")) or 0
                sold=await s.scalar(select(func.count()).select_from(Stock).where(Stock.product_id==pid,Stock.state=="SOLD")) or 0
                deleted=await s.scalar(select(func.count()).select_from(Stock).where(Stock.product_id==pid,Stock.state=="DELETED")) or 0
            await q.edit_message_text(
                f"📦 STOCK STATUS — {p.name if p else pid}\n\nAvailable: {available}\nSold: {sold}\nDeleted: {deleted}",
                reply_markup=markup([[("⬅️ Stock Menu","stock:"+str(pid))]]))
        elif d.startswith("delstock:"):
            if not await can(uid,"stock"): return await q.answer("Permission নেই",show_alert=True)
            pid=int(d.split(":")[1])
            async with Session() as s:
                count=await s.scalar(select(func.count()).select_from(Stock).where(Stock.product_id==pid,Stock.state=="AVAILABLE")) or 0
            await q.edit_message_text(
                f"⚠️ এই পণ্যের {count}টি Available stock Delete হবে। Sold stock ও Order history অক্ষত থাকবে।",
                reply_markup=markup([[("হ্যাঁ, Delete করুন","confirmdel:"+str(pid)),("Cancel","stock:"+str(pid))]]))
        elif d.startswith("confirmdel:"):
            if not await can(uid,"stock"): return await q.answer("Permission নেই",show_alert=True)
            pid=int(d.split(":")[1])
            async with Session() as s:
                items=(await s.scalars(select(Stock).where(Stock.product_id==pid,Stock.state=="AVAILABLE"))).all()
                for item in items: item.state="DELETED"
                await s.commit()
            await q.edit_message_text(f"✅ {len(items)}টি Available stock Delete করা হয়েছে।",
                reply_markup=markup([[("⬅️ Stock Menu","stock:"+str(pid))]]))
        elif d.startswith("stockhistory:"):
            if not await can(uid,"stock"): return await q.answer("Permission নেই",show_alert=True)
            pid=int(d.split(":")[1])
            async with Session() as s:
                batches=(await s.scalars(select(StockBatch).where(StockBatch.product_id==pid).order_by(StockBatch.id.desc()).limit(10))).all()
            lines=[f"#{b.id} | {b.mode} | Added {b.added_items}/{b.total_items} | {b.created_at:%Y-%m-%d %H:%M}" for b in batches]
            await q.edit_message_text("🕘 STOCK HISTORY\n\n"+("\n".join(lines) if lines else "কোনো History নেই।"),
                reply_markup=markup([[("⬅️ Stock Menu","stock:"+str(pid))]]))
        elif d.startswith("stockschema:"):
            if not await can(uid,"stock"): return await q.answer("Permission নেই",show_alert=True)
            pid=int(d.split(":")[1])
            async with Session() as s: schema=await s.get(ProductStockSchema,pid)
            current=schema.format_text if schema else "এখনও Stock Format সেট করা হয়নি।"
            WAIT[uid]=("schema",pid)
            await q.edit_message_text(
                f"🧾 Product Stock Format\n\nবর্তমান Format:\n{current}\n\nনতুন Format/নির্দেশনা লিখুন। উদাহরণ: Email | Password | Recovery Email\nএটি নির্দেশনা হিসেবে সংরক্ষিত হবে; Stock-এর মূল লেখা পরিবর্তন করবে না।")
        elif d.startswith("csv:"):
            if not await can(uid,"stock"): return await q.answer("Permission নেই",show_alert=True)
            WAIT[uid]=("csv",int(d.split(":")[1])); await q.edit_message_text("CSV file পাঠান।")
        elif d.startswith("manual:"):
            if not await can(uid,"stock"): return await q.answer("Permission নেই",show_alert=True)
            WAIT[uid]=("manual",int(d.split(":")[1])); await q.edit_message_text("প্রতিটি Stock item-এর মাঝে একটি খালি লাইন দিন।")
        elif d.startswith("hide:"):
            if not await can(uid,"products"): return await q.answer("Permission নেই",show_alert=True)
            async with Session() as s: p=await s.get(Product,int(d.split(":")[1])); p.active=False; await s.commit()
            await q.edit_message_text("Hidden করা হয়েছে।",reply_markup=markup([[("Back","roots")]]))
        elif d=="admins":
            if not owner(uid): return await q.answer("শুধু Owner",show_alert=True)
            await q.edit_message_text("👨‍💼 ADMIN MANAGEMENT\nএখান থেকে Admin নিয়োগ, Permission পরিবর্তন এবং Remove করতে পারবেন।",
                reply_markup=markup([[("➕ Add New Admin","adminadd")],[("📋 Admin List","adminlist")],[("⬅️ Back","admin")]]))
        elif d=="adminadd":
            if not owner(uid): return await q.answer("শুধু Owner",show_alert=True)
            WAIT[uid]=("adminadd",None)
            await q.edit_message_text("নতুন Admin-এর numeric Telegram User ID লিখুন।\nব্যক্তিকে আগে বট Start করতে বলুন।")
        elif d=="adminlist":
            if not owner(uid): return await q.answer("শুধু Owner",show_alert=True)
            async with Session() as s:
                admins=(await s.scalars(select(Admin).order_by(Admin.user_id))).all()
            rows=[]
            for a in admins:
                if a.user_id != OWNER_ID:
                    state="🟢 Active" if a.active else "🔴 Inactive"
                    rows.append([(f"{state} | {a.user_id}",f"admindetail:{a.user_id}")])
            rows.append([("⬅️ Back","admins")])
            await q.edit_message_text("📋 ADMIN LIST\nযাকে পরিচালনা করতে চান তাকে নির্বাচন করুন।",reply_markup=markup(rows))
        elif d.startswith("admindetail:"):
            if not owner(uid): return await q.answer("শুধু Owner",show_alert=True)
            target=int(d.split(":")[1])
            if target==OWNER_ID: return await q.answer("Owner পরিবর্তন করা যাবে না।",show_alert=True)
            async with Session() as s: a=await s.get(Admin,target)
            if not a: return await q.answer("Admin পাওয়া যায়নি।",show_alert=True)
            labels=[PERMS.get(k,k) for k in a.permissions.split(",") if k]
            await q.edit_message_text(
                f"👤 ADMIN DETAILS\nUser ID: {target}\nStatus: {'Active' if a.active else 'Inactive'}\nPermissions: {', '.join(labels) if labels else 'None'}",
                reply_markup=markup([[("✏️ Edit Permissions",f"editperms:{target}")],
                    [("🔴 Remove Admin" if a.active else "🟢 Reactivate Admin",f"toggleadmin:{target}")],
                    [("⬅️ Admin List","adminlist")]]))
        elif d.startswith("editperms:"):
            if not owner(uid): return await q.answer("শুধু Owner",show_alert=True)
            target=int(d.split(":")[1])
            if target==OWNER_ID: return await q.answer("Owner-এর Permission পরিবর্তন করা যাবে না।",show_alert=True)
            async with Session() as s: a=await s.get(Admin,target)
            if not a: return await q.answer("Admin পাওয়া যায়নি।",show_alert=True)
            selected={k:(k in a.permissions.split(",")) for k in PERMS}
            WAIT[uid]=("adminperms",{"target":target,"selected":selected})
            await show_permission_picker(q,selected,target)
        elif d.startswith("perm:"):
            if not owner(uid): return await q.answer("শুধু Owner",show_alert=True)
            _,key,target_text=d.split(":")
            target=int(target_text); state=WAIT.get(uid)
            if not state or state[0]!="adminperms" or state[1].get("target")!=target:
                return await q.answer("Permission session শেষ হয়েছে।",show_alert=True)
            selected=state[1]["selected"]; selected[key]=not selected.get(key,False)
            await show_permission_picker(q,selected,target)
        elif d.startswith("saveadmin:"):
            if not owner(uid): return await q.answer("শুধু Owner",show_alert=True)
            target=int(d.split(":")[1]); state=WAIT.get(uid)
            if not state or state[0]!="adminperms" or state[1].get("target")!=target:
                return await q.answer("Permission নির্বাচন পাওয়া যায়নি।",show_alert=True)
            permissions_text=",".join(k for k,v in state[1]["selected"].items() if v)
            async with Session() as s:
                a=await s.get(Admin,target)
                if not a:
                    a=Admin(user_id=target,permissions=permissions_text,active=True); s.add(a)
                else:
                    a.permissions=permissions_text; a.active=True
                await s.commit()
            WAIT.pop(uid,None)
            await q.edit_message_text(f"✅ Admin {target} সংরক্ষণ হয়েছে.\nPermissions: {permissions_text or 'None'}",
                reply_markup=markup([[("📋 Admin List","adminlist")],[("⬅️ Admin Management","admins")]]))
        elif d.startswith("toggleadmin:"):
            if not owner(uid): return await q.answer("শুধু Owner",show_alert=True)
            target=int(d.split(":")[1])
            if target==OWNER_ID: return await q.answer("Owner Remove করা যাবে না।",show_alert=True)
            async with Session() as s:
                a=await s.get(Admin,target)
                if not a: return await q.answer("Admin পাওয়া যায়নি।",show_alert=True)
                a.active=not a.active; active=a.active; await s.commit()
            WAIT.pop(uid,None)
            await q.edit_message_text(f"Admin {target} {'আবার Active' if active else 'Deactivate'} করা হয়েছে।",
                reply_markup=markup([[("📋 Admin List","adminlist")],[("⬅️ Admin Management","admins")]]))
        elif d=="deposits":
            if not await can(uid,"deposits"): return await q.answer("Permission নেই",show_alert=True)
            async with Session() as s: ds=(await s.scalars(select(Deposit).where(Deposit.state=="PENDING"))).all()
            rows=[[(f"#{x.id} User {x.user_id} {money(x.amount)}","dep:"+str(x.id))] for x in ds]+[[("Back","admin")]]
            await q.edit_message_text("Pending Deposits",reply_markup=markup(rows))
        elif d.startswith("dep:"):
            did=int(d.split(":")[1])
            if not await can(uid,"deposits"): return await q.answer("Permission নেই",show_alert=True)
            async with Session() as s: x=await s.get(Deposit,did)
            await q.edit_message_text(f"Deposit #{x.id}\nUser: {x.user_id}\nAmount: {money(x.amount)}\nTXID: {x.txid}",reply_markup=markup([[("Approve","approve:"+str(did)),("Reject","reject:"+str(did))]]))
        elif d.startswith(("approve:","reject:")):
            if not await can(uid,"deposits"): return await q.answer("Permission নেই",show_alert=True)
            approve=d.startswith("approve:"); did=int(d.split(":")[1])
            async with Session() as s:
                x=await s.get(Deposit,did)
                if not x or x.state!="PENDING": return await q.answer("Already processed",show_alert=True)
                x.state="APPROVED" if approve else "REJECTED"
                if approve:
                    u=await s.get(User,x.user_id); u.balance+=x.amount
                await s.commit()
            await context.bot.send_message(x.user_id,f"Deposit {'Approved' if approve else 'Rejected'}: {money(x.amount)}")
            await q.edit_message_text("Deposit processed.")
        elif d=="stats":
            if not await can(uid,"stats"): return await q.answer("Permission নেই",show_alert=True)
            async with Session() as s:
                users=await s.scalar(select(func.count()).select_from(User))
                orders=await s.scalar(select(func.count()).select_from(Order))
            await q.edit_message_text(f"📊 Users: {users}\nOrders: {orders}",reply_markup=markup([[("Back","admin")]]))
    except Exception:
        log.exception("Callback error")
        await q.message.reply_text("সমস্যা হয়েছে। Developer log পরীক্ষা করুন।")

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; state=WAIT.get(uid); text=update.message.text
    if not state:
        if text=="/admin":
            if await permissions(uid): await update.message.reply_text("ADMIN PANEL",reply_markup=admin_menu())
            else: await update.message.reply_text("Admin access নেই।")
        else: await update.message.reply_text("Menu ব্যবহার করুন।",reply_markup=customer_menu())
        return
    kind,val=state
    if kind=="deposit":
        try:
            a,tx=[v.strip() for v in text.split(",",1)]; value=Decimal(a)
            if not value.is_finite() or value<=0 or not tx or len(tx)>200: raise ValueError()
            amount=int(value*100)
        except Exception:
            return await update.message.reply_text("Format: 10, TX12345")
        async with Session() as s:
            dep=Deposit(user_id=uid,amount=amount,txid=tx); s.add(dep); await s.commit(); did=dep.id
        WAIT.pop(uid,None)
        await update.message.reply_text("Deposit request জমা হয়েছে।")
        await context.bot.send_message(OWNER_ID,f"Deposit #{did}\nUser: {uid}\nAmount: {money(amount)}\nTXID: {tx}",reply_markup=markup([[("Approve","approve:"+str(did)),("Reject","reject:"+str(did))]]))
    elif kind=="addroot":
        if not await can(uid,"products"):
            WAIT.pop(uid,None); return await update.message.reply_text("Product permission নেই।")
        if not text.strip(): return await update.message.reply_text("নাম খালি রাখা যাবে না।")
        async with Session() as s: s.add(Product(name=text.strip()[:150],kind="FOLDER")); await s.commit()
        WAIT.pop(uid,None); await update.message.reply_text("Main Product তৈরি হয়েছে।")
    elif kind=="child":
        if not await can(uid,"products"):
            WAIT.pop(uid,None); return await update.message.reply_text("Product permission নেই।")
        parent,typ=val
        if not text.strip(): return await update.message.reply_text("নাম খালি রাখা যাবে না।")
        async with Session() as s:
            if not await s.get(Product,parent):
                WAIT.pop(uid,None); return await update.message.reply_text("Parent product পাওয়া যায়নি।")
            s.add(Product(parent_id=parent,name=text.strip()[:150],kind=typ)); await s.commit()
        WAIT.pop(uid,None); await update.message.reply_text("Option তৈরি হয়েছে।")
    elif kind=="rename":
        if not await can(uid,"products"):
            WAIT.pop(uid,None); return await update.message.reply_text("Product permission নেই।")
        if not text.strip(): return await update.message.reply_text("নাম খালি রাখা যাবে না।")
        async with Session() as s:
            p=await s.get(Product,val)
            if not p: WAIT.pop(uid,None); return await update.message.reply_text("Product পাওয়া যায়নি।")
            p.name=text.strip()[:150]; await s.commit()
        WAIT.pop(uid,None); await update.message.reply_text("নাম পরিবর্তন হয়েছে।")
    elif kind=="price":
        if not await can(uid,"products"):
            WAIT.pop(uid,None); return await update.message.reply_text("Product permission নেই।")
        try:
            amount=Decimal(text.strip())
            if not amount.is_finite() or amount < 0 or amount > Decimal("99999999"):
                raise InvalidOperation()
            cents=int(amount*100)
        except (InvalidOperation, ValueError): return await update.message.reply_text("Price সঠিকভাবে লিখুন, যেমন 2.50")
        async with Session() as s:
            p=await s.get(Product,val)
            if not p: WAIT.pop(uid,None); return await update.message.reply_text("Product পাওয়া যায়নি।")
            p.price=cents; await s.commit()
        WAIT.pop(uid,None); await update.message.reply_text("Price সেট হয়েছে।")
    elif kind=="manual":
        if not await can(uid,"stock"):
            WAIT.pop(uid,None); return await update.message.reply_text("Stock permission নেই।")
        # Blank-line separated records; each record is stored and delivered without reformatting.
        import re
        records=[r.strip("\r\n") for r in re.split(r"\r?\n[ \t]*\r?\n", text) if r.strip()]
        added=0
        async with Session() as s:
            batch=StockBatch(product_id=val,admin_id=uid,mode="MANUAL",
                             filename="",total_items=len(records),added_items=0)
            s.add(batch); await s.flush()
            for record in records:
                digest=hashlib.sha256(record.encode("utf-8")).hexdigest()
                exists=await s.scalar(select(Stock.id).where(Stock.product_id==val,Stock.digest==digest))
                if exists: continue
                item=Stock(product_id=val,raw_text=record,digest=digest)
                s.add(item); await s.flush()
                s.add(StockPayload(stock_id=item.id,batch_id=batch.id,mode="MANUAL",
                                   header_bytes=None,record_bytes=record.encode("utf-8"),filename=""))
                added+=1
            batch.added_items=added
            await s.commit()
        WAIT.pop(uid,None)
        await update.message.reply_text(
            f"✅ Manual Stock Import সম্পন্ন।\nমোট Record: {len(records)}\nনতুন যোগ: {added}\nDuplicate বাদ: {len(records)-added}",
            reply_markup=markup([[("⬅️ Stock Menu","stock:"+str(val))]]))
    elif kind=="schema":
        if not await can(uid,"stock"):
            WAIT.pop(uid,None); return await update.message.reply_text("Stock permission নেই।")
        async with Session() as s:
            schema=await s.get(ProductStockSchema,val)
            if schema:
                schema.format_text=text
                schema.version+=1
                schema.updated_at=__import__("datetime").datetime.utcnow()
            else:
                s.add(ProductStockSchema(product_id=val,format_text=text,version=1))
            await s.commit()
        WAIT.pop(uid,None)
        await update.message.reply_text("✅ Product Stock Format সংরক্ষণ হয়েছে। মূল Stock content পরিবর্তন হবে না।",
            reply_markup=markup([[("⬅️ Stock Menu","stock:"+str(val))]]))
    elif kind=="adminadd":
        if not owner(uid): return
        try:
            target=int(text.strip())
            if target<=0 or target==OWNER_ID: raise ValueError()
        except ValueError:
            return await update.message.reply_text("সঠিক numeric Telegram User ID দিন। Owner ID দেওয়া যাবে না।")
        async with Session() as s: existing=await s.get(Admin,target)
        selected={k:(k in existing.permissions.split(",") if existing else False) for k in PERMS}
        WAIT[uid]=("adminperms",{"target":target,"selected":selected})
        rows=[[("✅ " if selected[k] else "⬜ ")+label,f"perm:{k}:{target}"] for k,label in PERMS.items()]
        rows += [[("💾 Save Admin",f"saveadmin:{target}")],[("Cancel","admins")]]
        await update.message.reply_text(f"Admin User ID: {target}\nPermission নির্বাচন করুন:",reply_markup=markup(rows))
    elif kind=="adminperms":
        return

async def document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    state=WAIT.get(uid)
    if not state or state[0]!="csv":
        return
    if not await can(uid,"stock"):
        WAIT.pop(uid,None)
        return await update.message.reply_text("Stock permission নেই।")
    pid=state[1]
    doc=update.message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".csv"):
        return await update.message.reply_text("দয়া করে .csv ফাইল পাঠান।")
    if doc.file_size and doc.file_size > 15 * 1024 * 1024:
        return await update.message.reply_text("CSV ফাইল 15 MB-এর কম হতে হবে।")
    try:
        telegram_file=await context.bot.get_file(doc.file_id)
        original_bytes=bytes(await telegram_file.download_as_bytearray())
        decoded=original_bytes.decode("utf-8")
        stream=io.StringIO(decoded, newline="")
        reader=csv.reader(stream)
        rows=[]
        for row in reader:
            rows.append((row,reader.line_num))
        if len(rows)<2:
            raise ValueError("Header এবং অন্তত একটি data row প্রয়োজন।")
        lines=decoded.splitlines(keepends=True)
        header_end=rows[0][1]
        header_bytes="".join(lines[:header_end]).encode("utf-8")
        record_entries=[]
        previous_line=header_end
        for row, end_line in rows[1:]:
            record_text="".join(lines[previous_line:end_line])
            previous_line=end_line
            if not record_text.strip():
                continue
            record_bytes=record_text.encode("utf-8")
            digest=hashlib.sha256(record_bytes).hexdigest()
            record_entries.append((row,record_text,record_bytes,digest))
        if not record_entries:
            raise ValueError("CSV-তে কোনো data row পাওয়া যায়নি।")
        added=0
        async with Session() as s:
            batch=StockBatch(product_id=pid,admin_id=uid,mode="CSV",
                filename=doc.file_name,total_items=len(record_entries),added_items=0,
                original_file=original_bytes)
            s.add(batch); await s.flush()
            for row,record_text,record_bytes,digest in record_entries:
                exists=await s.scalar(select(Stock.id).where(Stock.product_id==pid,Stock.digest==digest))
                if exists:
                    continue
                item=Stock(product_id=pid,raw_text=record_text,digest=digest)
                s.add(item); await s.flush()
                s.add(StockPayload(stock_id=item.id,batch_id=batch.id,mode="CSV",
                    header_bytes=header_bytes,record_bytes=record_bytes,filename=doc.file_name))
                added+=1
            batch.added_items=added
            await s.commit()
        WAIT.pop(uid,None)
        await update.message.reply_text(
            f"✅ CSV Import সম্পন্ন।\nমোট Data Row: {len(record_entries)}\nনতুন যোগ: {added}\nDuplicate বাদ: {len(record_entries)-added}\n\nমূল CSV ফাইল ও প্রতিটি Row-এর Byte content সংরক্ষণ করা হয়েছে।",
            reply_markup=markup([[("⬅️ Stock Menu","stock:"+str(pid))]]))
    except UnicodeDecodeError:
        await update.message.reply_text("এই V3 সংস্করণে UTF-8 CSV প্রয়োজন। Excel থেকে CSV UTF-8 হিসেবে Export করে আবার পাঠান।")
    except Exception as e:
        log.exception("CSV import failed")
        await update.message.reply_text(f"CSV Import ব্যর্থ: {e}")

async def post_init(app):
    await init_db()
    async with Session() as s:
        if OWNER_ID and not await s.get(Admin,OWNER_ID):
            s.add(Admin(user_id=OWNER_ID,permissions=",".join(PERMS),active=True))
            await s.commit()

def main():
    if not BOT_TOKEN or not OWNER_ID: raise RuntimeError("Set BOT_TOKEN and OWNER_ID in .env")
    app=Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("admin",admin_command))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.Document.ALL,document_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_message))
    app.run_polling()

async def admin_command(update, context):
    uid=update.effective_user.id
    if await permissions(uid): await update.message.reply_text("ADMIN PANEL",reply_markup=admin_menu())
    else: await update.message.reply_text("Admin access নেই।")

if __name__=="__main__": main()
