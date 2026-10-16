# Подпись кода (SignPath)

Чтобы Windows SmartScreen не показывал «Неизвестный издатель», `WallePlayer.exe` и установщик подписываются
сертификатом, которому Windows доверяет. Сертификаты крупных центров недоступны из РФ, поэтому подпись идёт через
**SignPath Foundation**: бесплатная подпись для open-source проектов. Издателем в окне Windows будет
«SignPath Foundation». Доверие SmartScreen накапливается по мере скачиваний и привязано к сертификату, а не к версии.

SignPath подписывает только то, что собрано на GitHub из публичного кода. Поэтому подписанный выпуск собирается
не на этом компьютере, а workflow [.github/workflows/release.yml](../.github/workflows/release.yml) в публичном
репозитории `mitay-walle/walle-player-desktop`.

## Как устроена сборка

1. GitHub Actions ставит пакеты из `requirements-build.txt`, берёт libmpv из последнего выпуска
   `shinchiro/mpv-winbuild-cmake` и собирает программу (`musicplayer.spec`).
2. `WallePlayer.exe` отправляется в SignPath (конфигурация артефакта `app`) и возвращается подписанным.
3. Установщик собирается вокруг уже подписанной программы: `scripts/build_installer.py --sign none`.
4. Установщик отправляется в SignPath (конфигурация `installer`) и прикладывается к выпуску GitHub для тега
   `v<версия>`.

Пока секреты SignPath не заданы, тот же workflow даёт неподписанный установщик только среди артефактов запуска
(удобно для проверки) и релиз не трогает: релиз выкладывается вручную из локальной сборки с самоподписанным
установщиком и `WallePlayer.cer`.

У обоих exe есть сведения о версии (Свойства → Подробно: Walle Player, версия, GPL-3.0), их пишет
`scripts/versioninfo.py`. SignPath сверяет имя продукта.

Локальная сборка (`scripts/build_installer.py`, по умолчанию `--sign self`) по-прежнему подписывает своим
самоподписанным сертификатом «CN=Walle Player». Ему доверяют только компьютеры, где установлен `WallePlayer.cer`.

## Что сделать один раз (делает владелец репозитория)

1. Подать заявку SignPath Foundation: https://signpath.org/apply. Указать репозиторий
   `https://github.com/mitay-walle/walle-player-desktop`, лицензию GPL-3.0 и сборку через GitHub Actions.
   Условия фонда: OSI-лицензия, проект поддерживается, на странице проекта есть политика подписи (раздел
   «Code signing policy» в README).
2. В SignPath, когда проект создадут:
   - подключить GitHub как доверенную систему сборки (Trusted Build System → GitHub.com) и связать её с проектом;
   - создать конфигурации артефактов `app` и `installer` из файлов
     [.signpath/artifact-configuration/app.xml](../.signpath/artifact-configuration/app.xml) и
     [installer.xml](../.signpath/artifact-configuration/installer.xml);
   - проверить слаги политик: `release-signing` (по умолчанию) и `test-signing`;
   - создать пользователя CI и его API-токен, добавить его отправителем (submitter) в политики.
3. В GitHub, в репозитории walle-player-desktop: Settings → Secrets and variables → Actions:
   - секрет `SIGNPATH_API_TOKEN` (токен пользователя CI);
   - переменная `SIGNPATH_ORGANIZATION_ID`;
   - при других слагах: `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_POLICY_SLUG`.
4. Выпуск: опубликовать исходники (`python scripts/publish_public.py "0.4.x" --push`), поставить тег `v0.4.x`
   и отправить его в публичный репозиторий. Подпись выпуска в политике `release-signing` подтверждает
   владелец (approver) в SignPath, потом workflow прикладывает подписанный установщик к выпуску.

## Code signing policy (текст для страницы проекта)

> Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
> [SignPath Foundation](https://signpath.org/).
>
> Committers and reviewers: [mitay-walle](https://github.com/mitay-walle). Approvers: the repository owner.
>
> Privacy policy: this program will not transfer any information to other networked systems unless specifically
> requested by the user or the person installing or operating it.
