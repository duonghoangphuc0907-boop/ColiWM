{
  lib,
  pkgs,
  python3Packages,
  dmenu,
  xterm,
  kdePackages.dolphin,
  gedit,
  makeWrapper,
  alacritty,
  firefox,
  xorg.xrandr,
}:

python3Packages.buildPythonApplication {
  pname = "coliwm";
  version = "0.10.0";
  pyproject = false;

  src = ./.;

  nativeBuildInputs = [ makeWrapper ];

  propagatedBuildInputs = with python3Packages; [
    xlib
  ];

  runtimeDeps = [
    dmenu
    xterm
  ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/bin /usr/share/xsessions

    cp coliconfig.py $out/bin/coliwm
    chmod +x $out/bin/coliwm

    wrapProgram $out/bin/coliwm \
      --prefix PATH : ${lib.makeBinPath runtimeDeps}

    cat > $out/share/xsessions/coliwm.desktop <<EOF
[Desktop Entry]
Name=ColiWM
Comment=Custom Python X11 Master-Stack Tiling Window Manager
Exec=$out/bin/coliwm
Type=Application
EOF

    runHook postInstall
  '';

  meta = with lib; {
    description = "Master-Stack Tiling Window Manager for X11 in Python";
    license = licenses.mit;
    platforms = platforms.linux;
    mainProgram = "coliwm";
  };
}
