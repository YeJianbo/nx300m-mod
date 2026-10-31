@rem Gradle start-up script for Windows
@rem Maintained by hand -- see gradle/wrapper/gradle-wrapper.properties
@if "%DEBUG%"=="" @echo off

set DIRNAME=%~dp0
if "%DIRNAME%"=="" set DIRNAME=.
set APP_BASE_NAME=%~n0
set APP_HOME=%DIRNAME%

set CLASSPATH=%APP_HOME%\gradle\wrapper\gradle-wrapper.jar

if not exist "%CLASSPATH%" (
  echo Error: cannot find "%CLASSPATH%" 1>&2
  exit /b 1
)

@rem Locate Java: prefer JAVA_HOME, fall back to PATH
if defined JAVA_HOME goto findJavaFromJavaHome
set JAVA_EXE=java.exe
%JAVA_EXE% -version >NUL 2>&1
if "%ERRORLEVEL%"=="0" goto execute
echo Error: Java not found. Install JDK 17+ and set JAVA_HOME. 1>&2
goto fail

:findJavaFromJavaHome
set JAVA_HOME=%JAVA_HOME:"=%
set JAVA_EXE=%JAVA_HOME%\bin\java.exe
if exist "%JAVA_EXE%" goto execute
echo Error: JAVA_HOME points to "%JAVA_HOME%" but java.exe is not there. 1>&2
goto fail

:execute
"%JAVA_EXE%" %DEFAULT_JVM_OPTS% %JAVA_OPTS% %GRADLE_OPTS% "-Dorg.gradle.appname=%APP_BASE_NAME%" -classpath "%CLASSPATH%" org.gradle.wrapper.GradleWrapperMain %*

if "%ERRORLEVEL%"=="0" goto end

:fail
exit /b 1

:end