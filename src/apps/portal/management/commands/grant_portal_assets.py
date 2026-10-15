from django.core.management.base import BaseCommand
from apps.assets.models import Asset
from apps.portal.models import PortalAccount, PortalAssetGrant


class Command(BaseCommand):
    help = "Grants organization assets to active client portal accounts so clients can select them when submitting requests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            default=None,
            help="Optional email of a specific client portal user (default: all active accounts)",
        )
        parser.add_argument(
            "--asset-tag",
            type=str,
            default=None,
            help="Optional asset tag of a specific asset to grant (default: all non-terminal assets)",
        )

    def handle(self, *args, **options):
        email = options.get("email")
        asset_tag = options.get("asset_tag")

        accounts = PortalAccount.objects.filter(is_enabled=True, membership__status="ACTIVE")
        if email:
            accounts = accounts.filter(membership__user__email__iexact=email)

        if not accounts.exists():
            self.stdout.write(self.style.WARNING("No matching active portal accounts found."))
            return

        total_granted = 0
        for account in accounts:
            org = account.organization
            assets = Asset.objects.filter(organization=org).exclude(status__in=["RETIRED", "DISPOSED"])
            if asset_tag:
                assets = assets.filter(asset_tag__iexact=asset_tag)

            for asset in assets:
                grant, created = PortalAssetGrant.objects.get_or_create(
                    organization=org,
                    portal_account=account,
                    asset=asset,
                )
                if created:
                    total_granted += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[GRANTED] {asset.asset_tag} ({asset.name}) -> {account.company_name} ({account.membership.user.email})"
                        )
                    )
                else:
                    self.stdout.write(
                        f"[EXISTS] {asset.asset_tag} already granted to {account.company_name}"
                    )

        self.stdout.write(self.style.SUCCESS(f"\nDone. Successfully added {total_granted} new asset grant(s)."))
