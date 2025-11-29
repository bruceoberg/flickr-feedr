{ pkgs, lib, config, inputs, ... }:

{
  packages = with pkgs;
  [
    exiftool
  ];

  languages.python =
  {
    enable = true;
    venv.enable = true;
    venv.requirements = ./requirements.txt;
  };

  # See full reference at https://devenv.sh/reference/options/
}
