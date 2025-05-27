# How to Manage Penpot Users

## Create a New Penpot User

After Penpot has been deployed, you can run the following command to
create a Penpot user. An email address and full name are required.

```
juju run penpot/leader create-profile email=john.doe@example.com fullname="John Doe"
```

The output of the action should look similar to the following:

```
Running operation 7 with 1 task
  - task 8 on unit-penpot-0

Waiting for task 8...
email: john.doe@example.com
fullname: John Doe
password: ZhrmqxSF74xVeA  # password should be different in your output
```

You can use the email and password from the output to log in to the
Penpot instance.

## Delete an Existing Penpot User

When a Penpot user is no longer in use, you can delete the user using
the following command:

```
juju run penpot/leader delete-profile email=john.doe@example.com
```

The output of the action should look similar to the following,
confirming the deleted user's email:

```
Running operation 9 with 1 task
  - task 10 on unit-penpot-1

Waiting for task 10...
email: john.doe@example.com
```
