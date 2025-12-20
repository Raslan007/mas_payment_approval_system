document.addEventListener("DOMContentLoaded", () => {
    const toggle = document.querySelector(".user-menu-toggle");
    const dropdown = document.querySelector(".user-menu-dropdown");

    if (toggle && dropdown) {
        const closeMenu = () => dropdown.classList.remove("show");

        toggle.addEventListener("click", (event) => {
            event.stopPropagation();
            dropdown.classList.toggle("show");
        });

        document.addEventListener("click", (event) => {
            if (!dropdown.contains(event.target) && !toggle.contains(event.target)) {
                closeMenu();
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closeMenu();
            }
        });
    }
});
