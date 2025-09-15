document.addEventListener('DOMContentLoaded', () => {
  // focus UX
  const firstInput = document.querySelector('input, select, textarea');
  if (firstInput) firstInput.focus();

  // auto-dismiss flash dopo 3s (indipendente da Bootstrap JS)
  const alerts = document.querySelectorAll('.alert');
  setTimeout(() => {
    alerts.forEach((el) => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 300);
    });
  }, 3000);
});
