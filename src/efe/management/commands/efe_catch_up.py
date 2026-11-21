from django.core.management.base import BaseCommand

from efe import catch_up


class Command(BaseCommand):
    help = "Queue daily tasks that did not run for more than a day (Mac app start-up)."

    def handle(self, *args, **options):
        names = catch_up.run()
        self.stdout.write("Kuyruğa alındı: " + (", ".join(names) if names else "yok"))
