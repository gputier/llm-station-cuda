# Security policy

## Reporting a vulnerability

Please report security issues **privately**, through GitHub's private
vulnerability reporting: open the **Security** tab of this repository and use
*Report a vulnerability*. That channel is private between you and the
maintainer.

Do not open a public issue or a pull request for a security problem.

Expect a first answer within a few days. This is a personal project, not a
product with an on-call rotation.

## What is in scope

The scripts in this repository, and the configuration they produce. Concretely:

- a setting that exposes the inference server more widely than the
  documentation claims;
- a command-line construction flaw that changes what the server is actually
  started with. There is a documented case of this: an empty argument was
  silently dropped, and the next argument slid into its place, so a hardening
  flag was deployed without doing anything;
- anything in this repository that leaks a credential, a hostname, or a private
  address.

## What is out of scope

Vulnerabilities in `llama.cpp` itself, in the model weights, or in CUDA and
Vulkan drivers. Report those upstream.

## Two things to know before deploying this

**The inference server binds `0.0.0.0`.** That is deliberate, since the client
sits on another machine, but it means the service is reachable from the whole
network segment.

**A firewall does not protect you from the browser case.** Any web page open on
any machine of your LAN can issue requests to a LAN-bound server behind its
user's back. If your network is not fully trusted, run the server with
`--api-key` and `--cors-origins ""`. The Vulkan sibling repository does exactly
that and documents how it was verified.
