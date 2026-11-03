// INTENTIONALLY VULNERABLE — test fixture for SecGenie. Do not copy.
const express = require("express");
const { exec } = require("child_process");

const app = express();

app.get("/calc", (req, res) => {
  // Code injection via eval on user input.
  res.send(String(eval(req.query.expr)));
});

app.get("/ls", (req, res) => {
  // Command injection via exec with user input.
  exec("ls " + req.query.dir, (err, stdout) => res.send(stdout));
});

app.listen(3000);
