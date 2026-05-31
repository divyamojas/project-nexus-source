
from fastapi import APIRouter

router = APIRouter(prefix="/legal", tags=["legal"])

PRIVACY_POLICY = """
Leaflet Privacy Policy
======================

Last updated: 2024-01-01

1. Information We Collect
   Leaflet collects information you provide directly, including your name, email address,
   and profile information when you register for an account. We also collect information
   about the books you list, borrow, and lend through our platform.

2. How We Use Your Information
   We use your information to operate and improve the Leaflet platform, facilitate book
   sharing between community members, send service-related communications, and enforce
   our terms of service.

3. Information Sharing
   We do not sell your personal information. We share information only to provide our
   service (e.g., showing your name to users you are lending to or borrowing from),
   or as required by law.

4. Data Retention
   We retain your information as long as your account is active. You may delete your
   account and associated data at any time via the app settings.

5. Security
   We take reasonable measures to protect your information, including encrypted
   connections (HTTPS) and access controls.

6. Contact
   For privacy concerns, please contact us at privacy@leaflet.app.
""".strip()

TERMS_OF_SERVICE = """
Leaflet Terms of Service
========================

Last updated: 2024-01-01

1. Acceptance of Terms
   By creating a Leaflet account, you agree to these Terms of Service. If you do not
   agree, do not use the platform.

2. Eligibility
   Leaflet is available to users with a valid @sprinklr.com or @gmail.com email address
   who have been approved by a platform administrator.

3. Community Standards
   You agree to treat fellow community members with respect. Misuse of the platform,
   including fraudulent loan activity or harassment, will result in account suspension.

4. Book Sharing
   Leaflet facilitates peer-to-peer book sharing. Users are responsible for the physical
   condition of books they lend and for returning borrowed books in a timely manner.

5. Disclaimers
   Leaflet is provided "as is." We do not guarantee the availability, accuracy, or
   quality of books listed on the platform.

6. Limitation of Liability
   To the maximum extent permitted by law, Leaflet is not liable for any indirect,
   incidental, or consequential damages arising from your use of the service.

7. Changes to Terms
   We may update these terms from time to time. Continued use of the platform after
   changes constitutes acceptance of the new terms.

8. Contact
   For questions about these terms, contact us at legal@leaflet.app.
""".strip()


@router.get("/privacy")
async def privacy_policy() -> dict:
    return {"content": PRIVACY_POLICY}


@router.get("/terms")
async def terms_of_service() -> dict:
    return {"content": TERMS_OF_SERVICE}
