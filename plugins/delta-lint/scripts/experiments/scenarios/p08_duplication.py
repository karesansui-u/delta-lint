"""⑧ Duplication Drift — 3重大度シナリオ.

high:   コピーされた email validator の一方が更新されてもう一方は古いまま
medium: 2つの price calculator で割引ロジックが微妙に異なる
low:    エラーメッセージ定数が2箇所に定義されていて文言が少し違う
"""

from experiments.framework import Scenario, Question

# =====================================================================
# ⑧ × high — Email validator のコピーが乖離
# =====================================================================

_H_AUTH_A = """\
# api/auth.py
import re
from services.auth_service import AuthService

auth_service = AuthService()

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$')

def register(request):
    \"\"\"POST /auth/register\"\"\"
    email = request.json.get("email", "").strip().lower()
    password = request.json.get("password", "")

    if not EMAIL_REGEX.match(email):
        return {"error": "Invalid email format"}, 400

    if len(password) < 8:
        return {"error": "Password must be at least 8 characters"}, 400

    user = auth_service.register(email=email, password=password)
    return {"user_id": user.id}, 201

def login(request):
    \"\"\"POST /auth/login\"\"\"
    email = request.json.get("email", "").strip().lower()
    password = request.json.get("password", "")
    token = auth_service.authenticate(email, password)
    if not token:
        return {"error": "Invalid credentials"}, 401
    return {"token": token}, 200
"""

_H_AUTH_B = """\
# api/auth.py
import re
from services.auth_service import AuthService

auth_service = AuthService()

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$')

# ⚠ DUPLICATION DRIFT: This EMAIL_REGEX was copied from services/newsletter_service.py
# 6 months ago. Since then, newsletter_service was updated to also accept emails with
# subaddressing (user+tag@domain.com) by adding '+' support, and to accept new TLDs
# up to 20 chars. But this copy was NEVER updated.
# Result: A user can subscribe to the newsletter with "user+work@example.com"
# but CANNOT register an account with the same email — the '+' is rejected here.
# Also, emails with long TLDs like .technology or .international are accepted by
# the newsletter but rejected by registration.

def register(request):
    \"\"\"POST /auth/register\"\"\"
    email = request.json.get("email", "").strip().lower()
    password = request.json.get("password", "")

    if not EMAIL_REGEX.match(email):
        return {"error": "Invalid email format"}, 400

    if len(password) < 8:
        return {"error": "Password must be at least 8 characters"}, 400

    user = auth_service.register(email=email, password=password)
    return {"user_id": user.id}, 201

def login(request):
    \"\"\"POST /auth/login\"\"\"
    email = request.json.get("email", "").strip().lower()
    password = request.json.get("password", "")
    token = auth_service.authenticate(email, password)
    if not token:
        return {"error": "Invalid credentials"}, 401
    return {"token": token}, 200
"""

_H_NEWSLETTER_A = """\
# api/newsletter.py
import re
from services.newsletter_service import NewsletterService

newsletter_service = NewsletterService()

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,20}$')

def subscribe(request):
    \"\"\"POST /newsletter/subscribe\"\"\"
    email = request.json.get("email", "").strip().lower()
    if not EMAIL_REGEX.match(email):
        return {"error": "Invalid email format"}, 400
    newsletter_service.subscribe(email)
    return {"status": "subscribed"}, 200
"""

_H_PROFILE = """\
# api/profile.py
from services.user_service import UserService

user_service = UserService()

def update_email(request):
    \"\"\"PATCH /profile/email — Change user's email.\"\"\"
    new_email = request.json.get("email", "").strip().lower()
    # Uses auth module's validation
    from api.auth import EMAIL_REGEX
    if not EMAIL_REGEX.match(new_email):
        return {"error": "Invalid email format"}, 400
    user_service.update_email(request.user_id, new_email)
    return {"email": new_email}, 200
"""

_H_MODEL = """\
# models/user.py
from dataclasses import dataclass
import uuid

@dataclass
class User:
    id: str = ""
    email: str = ""
    name: str = ""
    is_active: bool = True
"""

P08_HIGH = Scenario(
    pattern="⑧",
    pattern_name="Duplication Drift",
    severity="high",
    description="Email regex copied between auth and newsletter; newsletter updated to allow '+' but auth copy wasn't",
    visible_files={
        "api/auth.py": _H_AUTH_A,
        "api/newsletter.py": _H_NEWSLETTER_A,
        "api/profile.py": _H_PROFILE,
        "models/user.py": _H_MODEL,
    },
    annotated_files={
        "api/auth.py": _H_AUTH_B,
    },
    hidden_file_name="services/newsletter_service.py",
    hidden_file_description="Newsletter service's internal validation accepts '+' subaddressing and long TLDs",
    questions=[
        Question(
            text=(
                "A user subscribes to the newsletter with email 'alice+work@example.com' — success. "
                "They then try to register an account with the same email. What happens?"
            ),
            choices={
                "A": "Success — both use the same email validation rules",
                "B": "Failure — the auth registration rejects '+' in emails because its EMAIL_REGEX "
                     "is an older copy that doesn't support subaddressing",
                "C": "Success — but the '+work' part is stripped during registration",
                "D": "Failure — the email is already in use by the newsletter subscription",
            },
            correct="B",
        ),
        Question(
            text=(
                "A user with email 'user@company.technology' (11-char TLD) can subscribe to the "
                "newsletter. Can they also register an account?"
            ),
            choices={
                "A": "Yes — the auth regex allows TLDs of any length",
                "B": "No — the auth regex only allows TLDs up to 2+ characters (technically unlimited) "
                     "while the newsletter allows up to 20. Wait — the auth regex is {2,} which means "
                     "2 or more with no upper bound, so .technology would work",
                "C": "No — the auth regex limits TLDs to 4 characters",
                "D": "Yes — both regexes accept any valid TLD",
            },
            correct="D",
        ),
        Question(
            text=(
                "The profile update endpoint imports EMAIL_REGEX from api.auth. "
                "A user tries to change their email to 'bob+personal@gmail.com'. "
                "Does the update succeed?"
            ),
            choices={
                "A": "Yes — the profile endpoint has its own lenient validation",
                "B": "No — profile imports the auth module's EMAIL_REGEX which doesn't support '+'; "
                     "the email change is rejected",
                "C": "Yes — Gmail handles subaddressing server-side, not in validation",
                "D": "No — email changes are not allowed after registration",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑧ × medium — 割引計算ロジックが微妙に異なる
# =====================================================================

_M_CART_A = """\
# api/cart.py
from services.cart_service import CartService

cart_service = CartService()

def get_cart_total(request):
    \"\"\"GET /cart/total — Calculate cart total with discounts.\"\"\"
    cart = cart_service.get_cart(request.user_id)
    subtotal = sum(item.price * item.quantity for item in cart.items)

    # Apply discount
    discount = 0
    if cart.coupon_code:
        coupon = cart_service.get_coupon(cart.coupon_code)
        if coupon and coupon.type == "percentage":
            discount = subtotal * (coupon.value / 100)
        elif coupon and coupon.type == "fixed":
            discount = min(coupon.value, subtotal)

    total = subtotal - discount
    return {
        "subtotal": round(subtotal, 2),
        "discount": round(discount, 2),
        "total": round(total, 2),
    }, 200
"""

_M_CART_B = """\
# api/cart.py
from services.cart_service import CartService

cart_service = CartService()

# ⚠ DUPLICATION DRIFT: This discount calculation was copied from
# services/checkout_service.py. But checkout_service was later updated to:
# 1. Apply percentage discounts AFTER tax, not before
# 2. Cap percentage discounts at 50% maximum
# 3. Exclude sale items from coupon discounts
# This cart preview doesn't have any of these updates.
# Result: Cart shows $50 discount, but checkout only applies $25.

def get_cart_total(request):
    \"\"\"GET /cart/total — Calculate cart total with discounts.\"\"\"
    cart = cart_service.get_cart(request.user_id)
    subtotal = sum(item.price * item.quantity for item in cart.items)

    # Apply discount
    discount = 0
    if cart.coupon_code:
        coupon = cart_service.get_coupon(cart.coupon_code)
        if coupon and coupon.type == "percentage":
            discount = subtotal * (coupon.value / 100)
        elif coupon and coupon.type == "fixed":
            discount = min(coupon.value, subtotal)

    total = subtotal - discount
    return {
        "subtotal": round(subtotal, 2),
        "discount": round(discount, 2),
        "total": round(total, 2),
    }, 200
"""

_M_CHECKOUT = """\
# api/checkout.py
from services.checkout_service import CheckoutService

checkout_service = CheckoutService()

def process_checkout(request):
    \"\"\"POST /checkout\"\"\"
    result = checkout_service.calculate_and_charge(
        user_id=request.user_id,
        payment_token=request.json["payment_token"],
    )
    return result.to_dict(), 200
"""

_M_COUPON = """\
# models/coupon.py
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Coupon:
    code: str = ""
    type: str = "percentage"  # percentage or fixed
    value: float = 0.0
    max_discount_pct: float = 50.0  # Max percentage discount
    exclude_sale_items: bool = True
    expires_at: datetime = None
"""

_M_EMAIL = """\
# services/email_templates.py
def order_confirmation(order):
    return f\"\"\"
    Your order #{order.id}
    Subtotal: ${order.subtotal:.2f}
    Discount: -${order.discount:.2f}
    Tax: ${order.tax:.2f}
    Total: ${order.total:.2f}
    \"\"\"
"""

P08_MEDIUM = Scenario(
    pattern="⑧",
    pattern_name="Duplication Drift",
    severity="medium",
    description="Cart discount calc copied from checkout but checkout later added tax-after-discount, 50% cap, and sale exclusion",
    visible_files={
        "api/cart.py": _M_CART_A,
        "api/checkout.py": _M_CHECKOUT,
        "models/coupon.py": _M_COUPON,
        "services/email_templates.py": _M_EMAIL,
    },
    annotated_files={
        "api/cart.py": _M_CART_B,
    },
    hidden_file_name="services/checkout_service.py",
    hidden_file_description="calculate_and_charge() applies discount after tax, caps at 50%, excludes sale items",
    questions=[
        Question(
            text=(
                "A cart has $200 subtotal with a 60% off coupon. The cart preview shows "
                "discount=$120, total=$80. At checkout, what does the user actually pay?"
            ),
            choices={
                "A": "$80 — same as the cart preview",
                "B": "More than $80 — the checkout caps percentage discounts at 50%, so the "
                     "discount is $100 (not $120), plus it applies after tax",
                "C": "$0 — the 60% coupon combined with other promotions",
                "D": "$80 — but with tax added on top",
            },
            correct="B",
        ),
        Question(
            text=(
                "A cart contains 2 regular items ($50 each) and 1 sale item ($30). "
                "A 20% coupon is applied. The cart preview shows discount on full $130. "
                "What discount does checkout apply?"
            ),
            choices={
                "A": "$26 — 20% of $130 (all items)",
                "B": "$20 — 20% of $100 (only regular items); checkout excludes sale items "
                     "from coupon discounts but the cart preview doesn't",
                "C": "$26 — checkout doesn't distinguish between regular and sale items",
                "D": "$6 — 20% of the sale item only",
            },
            correct="B",
        ),
        Question(
            text=(
                "Customers frequently complain that 'the price changed at checkout'. "
                "Customer support checks the cart API and checkout API and confirms both "
                "are 'working correctly'. Is their analysis right?"
            ),
            choices={
                "A": "Yes — both APIs calculate correctly, the customer must be confused",
                "B": "No — both APIs work as coded, but they use DIFFERENT discount logic; "
                     "the cart preview is an outdated copy that doesn't match checkout's rules",
                "C": "Yes — but there's a timing issue where prices change between cart and checkout",
                "D": "No — the checkout has a bug in its discount calculation",
            },
            correct="B",
        ),
    ],
)


# =====================================================================
# ⑧ × low — エラーメッセージが2箇所で微妙に違う
# =====================================================================

_L_API_A = """\
# api/uploads.py
from services.upload_service import UploadService

upload_service = UploadService()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

def upload_file(request):
    \"\"\"POST /files/upload\"\"\"
    file = request.files.get("file")
    if not file:
        return {"error": "No file provided"}, 400

    if file.size > MAX_FILE_SIZE:
        return {"error": "File too large. Maximum size is 10MB."}, 413

    allowed = [".jpg", ".png", ".pdf", ".docx"]
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if f".{ext}" not in allowed:
        return {"error": f"File type '.{ext}' is not allowed. Accepted: .jpg, .png, .pdf, .docx"}, 400

    result = upload_service.store(file)
    return {"file_id": result.id, "url": result.url}, 201
"""

_L_API_B = """\
# api/uploads.py
from services.upload_service import UploadService

upload_service = UploadService()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# ⚠ DUPLICATION DRIFT: The validation here (10MB, .jpg/.png/.pdf/.docx) was copied
# from the upload service. But the service was later updated to also accept .gif and
# .webp, and the size limit was raised to 25MB for Pro users. This API layer still
# rejects .gif/.webp files and enforces the old 10MB limit for everyone.
# Users who see "supported: .gif" in the help docs get rejected by this layer.

def upload_file(request):
    \"\"\"POST /files/upload\"\"\"
    file = request.files.get("file")
    if not file:
        return {"error": "No file provided"}, 400

    if file.size > MAX_FILE_SIZE:
        return {"error": "File too large. Maximum size is 10MB."}, 413

    allowed = [".jpg", ".png", ".pdf", ".docx"]
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if f".{ext}" not in allowed:
        return {"error": f"File type '.{ext}' is not allowed. Accepted: .jpg, .png, .pdf, .docx"}, 400

    result = upload_service.store(file)
    return {"file_id": result.id, "url": result.url}, 201
"""

_L_HELP = """\
# docs/help.md
## File Uploads

Upload files via POST /files/upload.

**Supported formats:** .jpg, .png, .gif, .webp, .pdf, .docx
**Size limits:**
- Free: 10 MB
- Pro: 25 MB
"""

_L_MODEL = """\
# models/upload.py
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Upload:
    id: str = ""
    filename: str = ""
    size: int = 0
    mime_type: str = ""
    url: str = ""
    uploaded_at: datetime = None
"""

P08_LOW = Scenario(
    pattern="⑧",
    pattern_name="Duplication Drift",
    severity="low",
    description="API rejects .gif/.webp and enforces 10MB for all; service accepts them and allows 25MB for Pro",
    visible_files={
        "api/uploads.py": _L_API_A,
        "docs/help.md": _L_HELP,
        "models/upload.py": _L_MODEL,
    },
    annotated_files={
        "api/uploads.py": _L_API_B,
    },
    hidden_file_name="services/upload_service.py",
    hidden_file_description="Accepts .gif and .webp; 25MB limit for Pro users; the API layer blocks these before reaching service",
    questions=[
        Question(
            text=(
                "A user reads the help docs and tries to upload a .gif file via POST /files/upload. "
                "The docs say .gif is supported. What happens?"
            ),
            choices={
                "A": "Success — the upload service accepts .gif files",
                "B": "400 error — the API layer rejects .gif because its allowed list is outdated "
                     "(.jpg/.png/.pdf/.docx only); the request never reaches the upload service",
                "C": "Success — the API forwards all files to the service for validation",
                "D": "415 Unsupported Media Type — the server doesn't recognize .gif",
            },
            correct="B",
        ),
        Question(
            text=(
                "A Pro user tries to upload a 15MB PDF. The help docs say Pro limit is 25MB. "
                "What happens?"
            ),
            choices={
                "A": "Success — the 25MB Pro limit applies",
                "B": "413 error — the API layer enforces a hardcoded 10MB limit for ALL users, "
                     "rejecting the file before the service can apply the Pro tier's 25MB limit",
                "C": "Success — but the file is compressed to fit within 10MB",
                "D": "400 error — PDF files aren't allowed for uploads",
            },
            correct="B",
        ),
        Question(
            text=(
                "The team updates the upload service to accept .svg files and announces it "
                "in the changelog. Will users be able to upload .svg files?"
            ),
            choices={
                "A": "Yes — the service handles all validation",
                "B": "No — the API layer's hardcoded allowed list doesn't include .svg and will "
                     "reject it before the service sees the file; both layers need updating",
                "C": "Yes — after a server restart to pick up the new config",
                "D": "Depends on the user's file extension case sensitivity",
            },
            correct="B",
        ),
    ],
)
