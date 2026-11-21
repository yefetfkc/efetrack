"""'Oturumlar' settings page: open sessions and remote sign-out."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from efe import sessions


@require_GET
def sessions_page(request):
    key = request.session.session_key
    return render(request, "efe/sessions.html", {"sessions": sessions.list_sessions(request.user, key)})


@require_POST
def sessions_revoke(request):
    key = request.session.session_key
    if sessions.revoke(request.user, request.POST.get("id", ""), key):
        messages.success(request, "Oturum kapatıldı.")
    else:
        messages.error(request, "Bu oturum kapatılamadı.")
    return redirect("sessions")


@require_POST
def sessions_rename(request):
    if getattr(request.user, "is_demo", False):
        messages.error(request, "Demo hesabı yalnız görüntüleyebilir.")
    elif sessions.rename(request.user, request.POST.get("id", ""), request.POST.get("name", "")):
        name = sessions.clean_name(request.POST.get("name", ""))
        messages.success(request, f"Oturumun adı: {name}" if name else "Oturumun adı kaldırıldı.")
    else:
        messages.error(request, "Bu oturum bulunamadı.")
    return redirect("sessions")


@require_POST
def sessions_revoke_others(request):
    count = sessions.revoke_others(request.user, request.session.session_key)
    messages.success(request, f"{count} oturum kapatıldı." if count else "Kapatılacak başka oturum yok.")
    return redirect("sessions")
