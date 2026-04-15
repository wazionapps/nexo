(function () {
  const path = window.location.pathname || "";
  const is404 = path === "/404.html" || document.title.indexOf("404") !== -1;

  if (is404) {
    return;
  }

  const footer = document.querySelector("footer");
  if (!footer) {
    return;
  }

  function ensureTranslateContainer() {
    let container = document.getElementById("google_translate_element");
    if (container) {
      return container;
    }

    const shell = document.createElement("section");
    shell.className = "section-compact";
    shell.innerHTML = [
      '<div class="container" style="text-align:center;">',
      '  <p style="color:#6b7280;font-size:14px;margin-bottom:12px;">Translate this page</p>',
      '  <div id="google_translate_element"></div>',
      "</div>"
    ].join("");

    footer.parentNode.insertBefore(shell, footer);
    return shell.querySelector("#google_translate_element");
  }

  function loadTranslateScript() {
    if (document.querySelector('script[src*="translate.google.com/translate_a/element.js"]')) {
      return;
    }

    const script = document.createElement("script");
    script.src = "//translate.google.com/translate_a/element.js?cb=googleTranslateElementInit";
    script.async = true;
    document.body.appendChild(script);
  }

  window.googleTranslateElementInit = function () {
    if (!window.google || !google.translate) {
      return;
    }

    const target = ensureTranslateContainer();
    if (!target || target.dataset.initialized === "1") {
      return;
    }

    new google.translate.TranslateElement(
      {
        pageLanguage: "en",
        includedLanguages: "en,es,de,fr,it,pt,ja,ko,zh-CN,ar,ru",
        layout: google.translate.TranslateElement.InlineLayout.SIMPLE,
        autoDisplay: false
      },
      "google_translate_element"
    );

    target.dataset.initialized = "1";
  };

  ensureTranslateContainer();

  if (window.google && window.google.translate) {
    window.googleTranslateElementInit();
    return;
  }

  loadTranslateScript();
})();
