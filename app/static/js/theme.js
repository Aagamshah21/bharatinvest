// Light & Dark Mode Toggle Script
(function() {
  const savedTheme = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);
})();

document.addEventListener('DOMContentLoaded', () => {
  const toggleBtn = document.getElementById('themeToggleBtn');
  if (toggleBtn) {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
    toggleBtn.innerHTML = currentTheme === 'dark' ? '☀️ Light' : '🌙 Dark';

    toggleBtn.addEventListener('click', () => {
      const activeTheme = document.documentElement.getAttribute('data-theme');
      const newTheme = activeTheme === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', newTheme);
      localStorage.setItem('theme', newTheme);
      toggleBtn.innerHTML = newTheme === 'dark' ? '☀️ Light' : '🌙 Dark';
    });
  }
});
