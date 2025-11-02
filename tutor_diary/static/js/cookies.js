const banner = document.querySelector('[data-cookie-banner]');
const acceptBtn = document.querySelector('[data-cookie-accept]');
const declineBtn = document.querySelector('[data-cookie-decline]');
const COOKIE_KEY = 'tutor-diary-cookie';

const hideBanner = () => {
  if (banner) {
    banner.classList.remove('is-visible');
  }
};

const showBanner = () => {
  if (banner) {
    banner.classList.add('is-visible');
  }
};

const setCookiePreference = (value) => {
  const expires = new Date();
  expires.setFullYear(expires.getFullYear() + 1);
  document.cookie = `${COOKIE_KEY}=${value}; expires=${expires.toUTCString()}; path=/`;
};

const getCookiePreference = () => {
  const cookies = document.cookie.split(';').map((chunk) => chunk.trim());
  const preference = cookies.find((chunk) => chunk.startsWith(`${COOKIE_KEY}=`));
  return preference ? preference.split('=')[1] : null;
};

if (getCookiePreference() === null) {
  showBanner();
}

acceptBtn?.addEventListener('click', () => {
  setCookiePreference('accepted');
  hideBanner();
});

declineBtn?.addEventListener('click', () => {
  setCookiePreference('declined');
  hideBanner();
});
