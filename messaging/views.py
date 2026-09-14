from collections import OrderedDict

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from notifications.models import Notification
from notifications.services import notify

from .forms import MessageForm
from .models import ConversationArchive, Message
from .services import is_rate_limited


@login_required
def inbox(request):
	messages = (
		Message.objects.filter(Q(sender=request.user) | Q(recipient=request.user))
		.exclude(
			Exists(
				ConversationArchive.objects.filter(
					user=request.user,
					partner=OuterRef("sender"),
					created_at__gte=OuterRef("created_at"),
				)
			)
			| Exists(
				ConversationArchive.objects.filter(
					user=request.user,
					partner=OuterRef("recipient"),
					created_at__gte=OuterRef("created_at"),
				)
			)
		)
		.select_related("sender", "recipient")
	)
	conversations = OrderedDict()
	for message in messages.order_by("-created_at"):
		partner = message.recipient if message.sender_id == request.user.id else message.sender
		if partner.id not in conversations:
			conversations[partner.id] = {
				"partner": partner,
				"latest": message,
				"unread_count": 0,
			}
		if message.recipient_id == request.user.id and not message.is_read:
			conversations[partner.id]["unread_count"] += 1

	return render(request, "messaging/inbox.html", {"conversations": conversations.values()})


@login_required
def user_list(request):
	query = request.GET.get("q", "").strip()
	users = User.objects.exclude(pk=request.user.pk)
	if query:
		users = users.filter(
			Q(username__icontains=query)
			| Q(first_name__icontains=query)
			| Q(last_name__icontains=query)
		)

	return render(
		request,
		"messaging/user_list.html",
		{"users": users.order_by("username"), "query": query},
	)


@login_required
def conversation(request, username):
	partner = get_object_or_404(User, username=username)
	if partner == request.user:
		raise Http404
	ConversationArchive.objects.filter(user=request.user, partner=partner).delete()

	Message.objects.filter(
		sender=partner,
		recipient=request.user,
		is_read=False,
	).update(is_read=True)
	conversation_messages = Message.objects.filter(
		Q(sender=request.user, recipient=partner) | Q(sender=partner, recipient=request.user)
	).select_related("sender", "recipient")
	return render(
		request,
		"messaging/conversation.html",
		{"partner": partner, "conversation_messages": conversation_messages, "form": MessageForm()},
	)


@login_required
def archive_conversation(request, username):
	partner = get_object_or_404(User, username=username)
	if partner == request.user or not Message.objects.filter(
		Q(sender=request.user, recipient=partner) | Q(sender=partner, recipient=request.user)
	).exists():
		raise Http404
	if request.method == "POST":
		ConversationArchive.objects.get_or_create(user=request.user, partner=partner)
		Message.objects.filter(recipient=request.user, sender=partner, is_read=False).update(is_read=True)
	return redirect("messaging:inbox")


@login_required
def send_message(request, username):
	recipient = get_object_or_404(User, username=username)
	if recipient == request.user:
		raise Http404
	if request.method != "POST":
		return redirect("messaging:conversation", username=recipient.username)

	if is_rate_limited(request.user):
		django_messages.error(request, "You're sending messages too fast — please wait a moment.")
		conversation_filter = Q(sender=request.user, recipient=recipient) | Q(
			sender=recipient, recipient=request.user
		)
		conversation_messages = Message.objects.filter(conversation_filter).select_related(
			"sender", "recipient"
		)
		return render(
			request,
			"messaging/conversation.html",
			{
				"partner": recipient,
				"conversation_messages": conversation_messages,
				"form": MessageForm(),
			},
			status=429,
		)

	form = MessageForm(request.POST)
	if form.is_valid():
		message = form.save(commit=False)
		message.sender = request.user
		message.recipient = recipient
		message.save()
		notify(
			recipient=recipient,
			actor=request.user,
			verb=Notification.Verb.DM_RECEIVED,
			target=message,
		)
		return redirect("messaging:conversation", username=recipient.username)

	conversation_messages = Message.objects.filter(
		Q(sender=request.user, recipient=recipient) | Q(sender=recipient, recipient=request.user)
	).select_related("sender", "recipient")
	return render(
		request,
		"messaging/conversation.html",
		{"partner": recipient, "conversation_messages": conversation_messages, "form": form},
		status=400,
	)


@login_required
def unread_count(request):
	count = Message.objects.filter(recipient=request.user, is_read=False).exclude(
		Exists(
			ConversationArchive.objects.filter(
				user=request.user,
				partner=OuterRef("sender"),
				created_at__gte=OuterRef("created_at"),
			)
		)
	).count()
	return render(request, "messaging/_unread_count.html", {"unread_count": count})
