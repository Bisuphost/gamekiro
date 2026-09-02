# GameKiro Production Smoke Test

Use this checklist before promoting a build to production or staging.

- [ ] Homepage loads without errors.
- [ ] Signup works and creates a user profile.
- [ ] Welcome email task is queued via Celery.
- [ ] First-run profile setup loads for a new user.
- [ ] Profile setup can be completed or skipped.
- [ ] Login works.
- [ ] Logout works.
- [ ] Profile page loads.
- [ ] Games page loads.
- [ ] Forum loads.
- [ ] Messaging inbox loads.
- [ ] Notifications load, or the empty state is visible.
- [ ] Empty states render correctly for empty categories, messages, games, and profiles.
- [ ] 404 page renders cleanly.
- [ ] 500 page reviewed and not exposing stack traces.
- [ ] Mobile layout checked at 360px–480px widths.
- [ ] Tablet layout checked at 768px.
- [ ] Desktop layout checked at 1024px+.
- [ ] Static files load correctly.
- [ ] Media files upload and serve correctly.
- [ ] Celery worker is running.
- [ ] Database migrations are applied.
- [ ] Production settings use DEBUG=False.
- [ ] ALLOWED_HOSTS and SECRET_KEY are configured for the environment.
- [ ] Email provider is configured and SMTP/console backend is correct for the environment.
