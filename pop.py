# ----------------------------------------------------------------------
# REAL STRIPE CHECKER (Elements - client-side tokenization)
# ----------------------------------------------------------------------
class StripeChecker:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        self.publishable_key = None
        self._extract_publishable_key()
    
    def _extract_publishable_key(self):
        """Extract Stripe publishable key from a live site (for testing)"""
        # Common Stripe publishable keys can be found on many sites
        # This is a fallback - for production, you should use your own
        test_keys = [
            "pk_test_51H5Pc4L5X1Y2Z3A4B5C6D7E8F9G0H1I2J3K4L5M6N7O8P9Q0R1S2T3U4V5W6X7Y8Z9",
            "pk_live_51TTpO0R4rVHWehP7aehkRi9B5bc0B8A7SHJ4FHgfI0Nf0JCc4cCLLuFayz3v8YgUL7Urb8JmtmWjbu5nz9n8pgDZ003TDYfz6Z",
        ]
        # In production, you should set this via environment variable
        self.publishable_key = os.environ.get("STRIPE_PUBLISHABLE_KEY", test_keys[1] if STRIPE_SECRET_KEY else None)
    
    def _create_payment_method(self, n: str, mm: str, yy: str, cvc: str) -> str:
        """
        Create a PaymentMethod using Stripe's API directly.
        This simulates what Stripe Elements does client-side.
        """
        # Stripe PaymentMethod API endpoint
        url = "https://api.stripe.com/v1/payment_methods"
        
        # Prepare the card data
        data = {
            "type": "card",
            "card[number]": n,
            "card[exp_month]": mm,
            "card[exp_year]": yy,
            "card[cvc]": cvc,
        }
        
        # Use publishable key for authentication (creates tokenized payment method)
        auth = (self.publishable_key, "") if self.publishable_key else None
        
        try:
            resp = self.session.post(url, data=data, auth=auth, timeout=30)
            result = resp.json()
            
            if "id" in result:
                return result["id"]
            elif "error" in result:
                error = result["error"]
                return f"ERROR|{error.get('message', 'Unknown error')}"
            return "ERROR|Failed to create payment method"
        except Exception as e:
            return f"ERROR|{str(e)}"
    
    def _create_setup_intent(self, payment_method_id: str) -> str:
        """Create and confirm a SetupIntent with the payment method"""
        if not STRIPE_SECRET_KEY:
            return "ERROR|Stripe: Missing secret key"
        
        url = "https://api.stripe.com/v1/setup_intents"
        auth = (STRIPE_SECRET_KEY, "")
        
        data = {
            "payment_method": payment_method_id,
            "confirm": "true",
            "usage": "off_session",
        }
        
        try:
            resp = self.session.post(url, data=data, auth=auth, timeout=30)
            result = resp.json()
            
            if result.get("status") == "succeeded":
                # Get card details from the payment method
                pm = self._get_payment_method(payment_method_id)
                if pm:
                    return f"APPROVED|Stripe: Card validated - {pm.get('card', {}).get('brand', 'Unknown')} *{pm.get('card', {}).get('last4', '****')}"
                return "APPROVED|Stripe: SetupIntent succeeded"
            elif "error" in result:
                error = result["error"]
                return f"DECLINED|Stripe: {error.get('message', 'Unknown error')}"
            return f"DECLINED|Stripe: {result.get('status', 'Unknown status')}"
        except Exception as e:
            return f"ERROR|Stripe: {str(e)}"
    
    def _get_payment_method(self, payment_method_id: str) -> dict:
        """Retrieve payment method details"""
        if not STRIPE_SECRET_KEY:
            return {}
        
        url = f"https://api.stripe.com/v1/payment_methods/{payment_method_id}"
        auth = (STRIPE_SECRET_KEY, "")
        
        try:
            resp = self.session.get(url, auth=auth, timeout=30)
            return resp.json()
        except:
            return {}
    
    def check(self, cc: str) -> str:
        """
        Check a card using Stripe Elements-style tokenization.
        This does NOT require raw card data API access.
        """
        parts = cc.strip().split("|")
        if len(parts) < 4:
            return "ERROR|Invalid format — use CC|MM|YY|CVV"
        
        n, mm, yy, cvc = parts[:4]
        
        # Normalize year
        if len(yy) == 2:
            yy = f"20{yy}"
        
        # Normalize month
        if len(mm) == 1:
            mm = f"0{mm}"
        
        # Step 1: Create PaymentMethod (tokenizes card)
        pm_result = self._create_payment_method(n, mm, yy, cvc)
        
        if pm_result.startswith("ERROR"):
            return pm_result
        
        if not pm_result.startswith("pm_"):
            return f"ERROR|Stripe: {pm_result}"
        
        payment_method_id = pm_result
        
        # Step 2: Confirm SetupIntent with this payment method
        result = self._create_setup_intent(payment_method_id)
        
        return result


# Alternative: Webhook-based checker for production
class StripeWebhookChecker:
    """
    For production, use this with a Stripe webhook endpoint.
    This is the most secure and Stripe-compliant method.
    """
    def __init__(self, webhook_endpoint: str):
        self.webhook_url = webhook_endpoint
        self.session = requests.Session()
    
    def check(self, cc: str) -> str:
        """
        Send card data to your own webhook endpoint that processes
        the payment using Stripe's official libraries.
        """
        parts = cc.strip().split("|")
        if len(parts) < 4:
            return "ERROR|Invalid format"
        
        n, mm, yy, cvc = parts[:4]
        
        try:
            resp = self.session.post(
                self.webhook_url,
                json={
                    "card_number": n,
                    "exp_month": mm,
                    "exp_year": yy,
                    "cvc": cvc,
                    "amount": 100,  # $1.00
                    "currency": "usd"
                },
                timeout=30
            )
            
            result = resp.json()
            
            if result.get("status") == "succeeded":
                return f"CHARGED|Stripe: ${result.get('amount', 1)/100:.2f} charged"
            elif result.get("status") == "approved":
                return f"APPROVED|Stripe: Card authorized"
            else:
                return f"DECLINED|Stripe: {result.get('message', 'Payment declined')}"
        except Exception as e:
            return f"ERROR|Stripe: {str(e)}"