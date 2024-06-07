#! /bin/bash

Xvnc -SecurityTypes none -localhost :100 -nocursor -geometry 1568x800 &
disown

